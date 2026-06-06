import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.src import models, schemas
from backend.src.database import Base
from backend.src.agent.context import ConversationContext
from backend.src.agent.engine import AUTO_GREETING_TEXT, AgentEngine
from backend.src.agent.types import ConversationSnapshot, ConversationState, HistoryMessage, TenantInfo, TicketResult
from backend.src.routers.webhook import greenapi_webhook
from backend.src.services.adapters import SqlConversationStore, WhatsAppNotificationService
from backend.src.services.buffer import BufferedMessage, MessageBuffer
from backend.src.services import scheduler as scheduler_mod
from backend.src.routers.technicians import require_self_technician
from backend.src.routers import agents as agents_router
from backend.src.routers import tickets as tickets_router
from backend.src.routers import technicians as technicians_router


class ConversationHistoryTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        self.db = session_factory()

        tenant = models.Tenant(
            id=1,
            name="Tenant",
            phone="77700000000",
            apartment="1",
            agent_enabled=True,
        )
        conversation = models.Conversation(
            id=1,
            tenant_id=1,
            whatsapp_chat_id="77700000000@c.us",
        )
        self.db.add_all([tenant, conversation])
        self.db.commit()
        self.store = SqlConversationStore(self.db)

    def tearDown(self):
        self.db.close()

    def test_history_returns_latest_twenty_in_chronological_order(self):
        base_time = datetime(2026, 5, 17, tzinfo=timezone.utc)
        for index in range(25):
            self.db.add(models.Message(
                conversation_id=1,
                sender=models.MessageSenderEnum.tenant,
                message_type=models.MessageTypeEnum.text,
                content=f"msg {index:02d}",
                created_at=base_time + timedelta(minutes=index),
            ))
        self.db.commit()

        history = self.store.get_message_history(1)

        self.assertEqual(len(history), 20)
        self.assertEqual(history[0].content, "msg 05")
        self.assertEqual(history[-1].content, "msg 24")

    def test_history_preserves_image_only_messages(self):
        self.db.add(models.Message(
            conversation_id=1,
            sender=models.MessageSenderEnum.tenant,
            message_type=models.MessageTypeEnum.image,
            content="",
            media_url="data:image/jpeg;base64,abc",
        ))
        self.db.commit()

        history = self.store.get_message_history(1)

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].content, "[Фото]")
        self.assertTrue(history[0].has_image)

    def test_stale_open_conversation_resets_context_after_seven_days(self):
        conv = self.db.query(models.Conversation).one()
        conv.state = models.ConversationStateEnum.service_scheduling
        conv.scenario = models.ScenarioEnum.service
        conv.context_data = {
            "response_id": "resp_old",
            "ticket_number": "TKT-OLD",
            "offered_slots": [{"start": "old"}],
        }
        self.db.add(models.Message(
            conversation_id=1,
            sender=models.MessageSenderEnum.tenant,
            message_type=models.MessageTypeEnum.text,
            content="old topic",
            created_at=datetime.now(timezone.utc) - timedelta(days=8),
        ))
        self.db.commit()

        snapshot = self.store.get_or_create_conversation(1, "77700000000@c.us")

        self.assertEqual(snapshot.state, ConversationState.new_conversation)
        self.assertEqual(snapshot.context_data, {"_pending_greeting": True})
        self.assertIsNone(snapshot.scenario)
        self.assertIsNotNone(snapshot.reopened_at)

    def test_recent_open_conversation_keeps_context_before_seven_days(self):
        conv = self.db.query(models.Conversation).one()
        conv.state = models.ConversationStateEnum.service_scheduling
        conv.scenario = models.ScenarioEnum.service
        conv.context_data = {"response_id": "resp_recent", "problem": "recent leak"}
        self.db.add(models.Message(
            conversation_id=1,
            sender=models.MessageSenderEnum.tenant,
            message_type=models.MessageTypeEnum.text,
            content="recent topic",
            created_at=datetime.now(timezone.utc) - timedelta(days=6),
        ))
        self.db.commit()

        snapshot = self.store.get_or_create_conversation(1, "77700000000@c.us")

        self.assertEqual(snapshot.state, ConversationState.service_scheduling)
        self.assertEqual(snapshot.context_data["response_id"], "resp_recent")
        self.assertEqual(snapshot.context_data["problem"], "recent leak")


class BufferTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiting_test_messages_do_not_send_whatsapp_reply(self):
        buffer = MessageBuffer()
        chat_id = "77700000000@c.us"
        chat_buffer = buffer._get_buffer(chat_id)
        chat_buffer.messages.append(BufferedMessage(
            phone="77700000000",
            content="hello",
            send_reply=False,
        ))
        future = asyncio.get_running_loop().create_future()
        chat_buffer.result_futures.append(future)

        with (
            patch.object(MessageBuffer, "_process_sync", return_value=("reply", "gathering", None)),
            patch("backend.src.services.buffer.send_whatsapp_reply") as send_reply,
        ):
            await buffer._flush(chat_id)

        self.assertEqual(await future, ("reply", "gathering", None))
        send_reply.assert_not_called()

    async def test_production_messages_send_whatsapp_reply(self):
        buffer = MessageBuffer()
        chat_id = "77700000000@c.us"
        chat_buffer = buffer._get_buffer(chat_id)
        chat_buffer.messages.append(BufferedMessage(
            phone="77700000000",
            content="hello",
            send_reply=True,
        ))

        with (
            patch.object(MessageBuffer, "_process_sync", return_value=("reply", "gathering", None)),
            patch("backend.src.services.buffer.send_whatsapp_reply") as send_reply,
        ):
            await buffer._flush(chat_id)

        send_reply.assert_called_once_with(chat_id, "reply")


class TechnicianRoleGuardTests(unittest.TestCase):
    def test_self_technician_guard_rejects_non_technicians(self):
        user = models.User(role=models.RoleEnum.agent)

        with self.assertRaises(Exception) as raised:
            require_self_technician(user)

        self.assertEqual(getattr(raised.exception, "status_code", None), 403)

    def test_self_technician_guard_allows_technicians(self):
        user = models.User(role=models.RoleEnum.technician)

        require_self_technician(user)


class OperatorPauseWebhookTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        self.db = session_factory()
        self.db.add(models.Tenant(
            id=1,
            name="Tenant",
            phone="77700000000",
            apartment="1",
            agent_enabled=True,
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    async def test_outgoing_phone_message_pauses_operator_chat(self):
        body = {
            "typeWebhook": "outgoingMessageReceived",
            "senderData": {"chatId": "77700000000@c.us"},
            "messageData": {
                "typeMessage": "textMessage",
                "textMessageData": {"textMessage": "Operator here"},
            },
        }

        with (
            patch("backend.src.routers.webhook.SessionLocal", return_value=self.db),
            patch(
                "backend.src.routers.webhook.create_agent_engine",
                return_value=SimpleNamespace(store=SqlConversationStore(self.db)),
            ),
            patch("backend.src.routers.webhook.message_buffer.add_message") as add_message,
        ):
            response = await greenapi_webhook(body)

        self.assertEqual(response["status"], "ok")
        self.assertTrue(response["operator_paused"])
        add_message.assert_not_called()

        conversation = self.db.query(models.Conversation).one()
        self.assertIn("operator_paused_until", conversation.context_data)
        admin_message = self.db.query(models.Message).one()
        self.assertEqual(admin_message.sender, models.MessageSenderEnum.admin)
        self.assertEqual(admin_message.content, "Operator here")

    async def test_outgoing_api_message_does_not_pause(self):
        body = {
            "typeWebhook": "outgoingAPIMessageReceived",
            "senderData": {"chatId": "77700000000@c.us"},
            "messageData": {
                "typeMessage": "textMessage",
                "textMessageData": {"textMessage": "Agent reply"},
            },
        }

        with patch("backend.src.routers.webhook.SessionLocal", return_value=self.db):
            response = await greenapi_webhook(body)

        self.assertEqual(response["status"], "ignored")
        self.assertEqual(self.db.query(models.Conversation).count(), 0)

    async def test_outgoing_group_message_is_ignored(self):
        body = {
            "typeWebhook": "outgoingMessageReceived",
            "senderData": {"chatId": "120363000@g.us"},
            "messageData": {"typeMessage": "textMessage", "textMessageData": {"textMessage": "Hi"}},
        }

        with patch("backend.src.routers.webhook.SessionLocal", return_value=self.db):
            response = await greenapi_webhook(body)

        self.assertEqual(response["status"], "ignored")
        self.assertEqual(response["reason"], "group_chat")
        self.assertEqual(self.db.query(models.Conversation).count(), 0)


class FakeStore:
    def __init__(self):
        self.updated = []

    def update_conversation(self, conversation_id, update):
        self.updated.append((conversation_id, update))


class FailingLLM:
    def run(self, *args, **kwargs):
        raise AssertionError("LLM should not be called while operator pause is active")


class OperatorPauseAgentTests(unittest.TestCase):
    def make_context(self, paused_until: datetime) -> tuple[ConversationContext, FakeStore]:
        snapshot = ConversationSnapshot(
            id=1,
            tenant_id=1,
            chat_id="77700000000@c.us",
            status="open",
            state=ConversationState.gathering,
            scenario=None,
            context_data={"operator_paused_until": paused_until.isoformat()},
            escalated_at=None,
            reopened_at=None,
        )
        tenant = TenantInfo(
            id=1,
            name="Tenant",
            phone="77700000000",
            building_name="Building",
            apartment="1",
            agent_enabled=True,
        )
        store = FakeStore()
        ctx = ConversationContext(snapshot, tenant, "77700000000")
        return ctx, store

    def test_active_operator_pause_skips_llm(self):
        ctx, store = self.make_context(datetime.now(timezone.utc) + timedelta(minutes=30))
        engine = AgentEngine(store=store, scheduler=None, notifier=None, llm=FailingLLM())

        reply, state, result = engine.process_conversation(ctx)

        self.assertEqual(reply, "")
        self.assertEqual(state, "operator_paused")
        self.assertIsNone(result)
        self.assertEqual(store.updated, [])

    def test_expired_operator_pause_is_cleared(self):
        ctx, store = self.make_context(datetime.now(timezone.utc) - timedelta(minutes=1))

        class EmptyHistoryStore(FakeStore):
            def get_message_history(self, conversation_id, since=None):
                return []

        store = EmptyHistoryStore()
        engine = AgentEngine(store=store, scheduler=None, notifier=None, llm=FailingLLM())

        reply, state, result = engine.process_conversation(ctx)

        self.assertEqual(reply, "")
        self.assertEqual(state, "gathering")
        self.assertIsNone(result)
        self.assertNotIn("operator_paused_until", ctx.context_data)
        self.assertEqual(len(store.updated), 1)


class InternetEscalationAgentTests(unittest.TestCase):
    def test_internet_issue_escalates_without_calling_llm_or_ticket_tools(self):
        class InternetStore(FakeStore):
            def __init__(self):
                super().__init__()
                self.saved = []

            def get_message_history(self, conversation_id, since=None):
                return [HistoryMessage(role="tenant", content="У меня не работает интернет", has_image=False)]

            def save_message(self, conversation_id, sender, content, image_base64=None):
                self.saved.append((conversation_id, sender, content))

        tenant = TenantInfo(
            id=1,
            name="Tenant",
            phone="77700000000",
            building_name="Building",
            apartment="1",
            agent_enabled=True,
        )
        snapshot = ConversationSnapshot(
            id=1,
            tenant_id=1,
            chat_id="77700000000@c.us",
            status="open",
            state=ConversationState.gathering,
            scenario=None,
            context_data={},
            escalated_at=None,
            reopened_at=None,
        )
        ctx = ConversationContext(snapshot, tenant, "77700000000")
        store = InternetStore()
        notifier = CapturingNotifier()
        engine = AgentEngine(store=store, scheduler=None, notifier=notifier, llm=FailingLLM())

        reply, state, result = engine.process_conversation(ctx)

        self.assertEqual(state, "escalated_to_human")
        self.assertIn("оператору", reply)
        self.assertTrue(result.requires_human)
        self.assertEqual(result.subtype, "internet")
        self.assertEqual(len(notifier.escalations), 1)
        self.assertEqual(len(store.updated), 1)
        self.assertEqual(store.saved[0][1], "ai")


class CapturingMessageLLM:
    def __init__(self):
        self.calls = []

    def generate_message(self, prompt_name, user_content, fallback):
        self.calls.append((prompt_name, user_content, fallback))
        return "Сообщение технику на русском языке"


class TechnicianAssignmentLanguageTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        self.db = session_factory()
        self.db.add(models.User(
            id="tech-1",
            name="Almat",
            email="almat@example.com",
            phone="+77770000000",
            password_hash="x",
            role=models.RoleEnum.technician,
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_technician_assignment_forces_russian_output(self):
        llm = CapturingMessageLLM()
        service = WhatsAppNotificationService(self.db, llm)
        tenant = TenantInfo(
            id=1,
            name="John",
            phone="77700000000",
            building_name="Esentai",
            apartment="12",
            agent_enabled=True,
            building_address="Main street",
            building_house_number="5",
            building_floor="3",
            building_block="A",
        )

        with patch("backend.src.services.adapters.notifier_mod.send_whatsapp_reply") as send_reply:
            sent = service.notify_technician_assigned(
                technician_name="Almat",
                ticket_number="TKT-12345678",
                tenant=tenant,
                description="Kitchen sink is leaking badly",
                category="plumbing",
                urgency="high",
                scheduled_time="2026-05-17T15:00:00+05:00",
            )

        self.assertTrue(sent)
        self.assertEqual(llm.calls[0][0], "technician_assignment")
        user_content = llm.calls[0][1]
        self.assertIn("ONLY in Russian", user_content)
        self.assertIn("Translate all tenant-provided text", user_content)
        self.assertIn("Kitchen sink is leaking badly", user_content)
        send_reply.assert_called_once_with("77770000000@c.us", "Сообщение технику на русском языке")


class TenantCreationGreetingTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        self.db = session_factory()
        self.agent = models.User(
            id="agent-1",
            name="Agent",
            email="agent@example.com",
            password_hash="x",
            role=models.RoleEnum.agent,
        )
        self.building = models.Building(
            id=1,
            name="Building",
            address="Address",
            owner_id="agent-1",
        )
        self.db.add_all([self.agent, self.building])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_create_tenant_sends_and_records_greeting_when_agent_enabled(self):
        body = agents_router.TenantCreateRequest(
            name="New Tenant",
            phone="87001234567",
            apartment="10",
            building_id=1,
            agent_enabled=True,
        )

        with patch("backend.src.routers.agents.notifier.send_whatsapp_reply") as send_reply:
            result = agents_router.create_tenant(body, current_user=self.agent, db=self.db)

        self.assertEqual(result.name, "New Tenant")
        send_reply.assert_called_once_with("77001234567@c.us", AUTO_GREETING_TEXT)

        conversation = self.db.query(models.Conversation).one()
        self.assertEqual(conversation.whatsapp_chat_id, "77001234567@c.us")
        self.assertTrue(conversation.context_data["greeting_sent"])
        message = self.db.query(models.Message).one()
        self.assertEqual(message.sender, models.MessageSenderEnum.ai)
        self.assertEqual(message.content, AUTO_GREETING_TEXT)

    def test_create_tenant_does_not_greet_when_agent_disabled(self):
        body = agents_router.TenantCreateRequest(
            name="Silent Tenant",
            phone="77007654321",
            apartment="11",
            building_id=1,
            agent_enabled=False,
        )

        with patch("backend.src.routers.agents.notifier.send_whatsapp_reply") as send_reply:
            result = agents_router.create_tenant(body, current_user=self.agent, db=self.db)

        self.assertEqual(result.name, "Silent Tenant")
        send_reply.assert_not_called()
        self.assertEqual(self.db.query(models.Conversation).count(), 0)
        self.assertEqual(self.db.query(models.Message).count(), 0)


class TicketApiRegressionTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        self.db = session_factory()
        self.dispatcher = models.User(
            id="dispatcher-1",
            name="Dispatcher",
            email="dispatcher@example.com",
            password_hash="x",
            role=models.RoleEnum.dispatcher,
        )
        self.agent = models.User(
            id="agent-1",
            name="Agent",
            email="agent@example.com",
            password_hash="x",
            role=models.RoleEnum.agent,
        )
        self.tech = models.User(
            id="tech-1",
            name="Technician One",
            email="tech@example.com",
            phone="+77770000001",
            password_hash="x",
            role=models.RoleEnum.technician,
        )
        self.other_tech = models.User(
            id="tech-2",
            name="Technician Two",
            email="tech2@example.com",
            phone="+77770000002",
            password_hash="x",
            role=models.RoleEnum.technician,
        )
        self.building = models.Building(id=1, name="Building A", address="Main")
        other_building = models.Building(id=2, name="Building B", address="Side")
        self.tenant = models.Tenant(id=1, name="Alice Tenant", phone="77700000001", apartment="10", building_id=1)
        other_tenant = models.Tenant(id=2, name="Bob Tenant", phone="77700000002", apartment="20", building_id=2)
        self.db.add_all([
            self.dispatcher, self.agent, self.tech, self.other_tech,
            self.building, other_building, self.tenant, other_tenant,
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _add_ticket(
        self,
        number: str,
        tenant_id: int,
        created_at: datetime,
        scheduled_time: datetime | None = None,
        assigned_to: str | None = None,
    ) -> models.Ticket:
        ticket = models.Ticket(
            ticket_number=number,
            tenant_id=tenant_id,
            category="Plumbing",
            urgency="LOW",
            description=number,
            availability_time=None,
            status=models.TicketStatusEnum.new,
            scheduled_time=scheduled_time,
            assigned_to=assigned_to,
            created_at=created_at,
        )
        self.db.add(ticket)
        self.db.commit()
        self.db.refresh(ticket)
        return ticket

    def test_ticket_list_sorts_by_scheduled_time_and_exposes_tenant_name(self):
        base = datetime(2026, 5, 17, 8, tzinfo=timezone.utc)
        self._add_ticket("TKT-NO-DATE", 1, base + timedelta(hours=3), None)
        self._add_ticket("TKT-LATE", 1, base + timedelta(hours=2), base + timedelta(days=2))
        self._add_ticket("TKT-EARLY", 1, base + timedelta(hours=1), base + timedelta(days=1))

        result = tickets_router.read_tickets(db=self.db, current_user=self.dispatcher)

        self.assertEqual([t.id for t in result], ["TKT-EARLY", "TKT-LATE", "TKT-NO-DATE"])
        self.assertEqual(result[0].tenantName, "Alice Tenant")
        self.assertEqual(result[0].tenantId, 1)

    def test_create_ticket_accepts_missing_availability_and_assigns_technician(self):
        body = schemas.TicketCreate(
            tenant_id=1,
            category="Plumbing",
            urgency="medium",
            description="Leak",
            assigned_to="tech-1",
        )

        with (
            patch("backend.src.routers.tickets.notifier.generate_technician_assignment_message", return_value="message") as generate_message,
            patch("backend.src.routers.tickets.notifier.notify_technician") as notify_technician,
        ):
            result = tickets_router.create_ticket(body, db=self.db, current_user=self.dispatcher)

        ticket = self.db.query(models.Ticket).filter(models.Ticket.ticket_number == result.id).one()
        self.assertIsNone(ticket.availability_time)
        self.assertEqual(ticket.assigned_to, "tech-1")
        self.assertEqual(ticket.status, models.TicketStatusEnum.assigned)
        generate_message.assert_called_once()
        notify_technician.assert_called_once_with(self.db, "tech-1", "message")

    def test_create_ticket_rejects_scheduled_time_without_assignee(self):
        target_date = (datetime.now(scheduler_mod.TZ_ALMATY) + timedelta(days=1)).date()
        target_time = datetime(target_date.year, target_date.month, target_date.day, 9, 0)
        body = schemas.TicketCreate(
            tenant_id=1,
            category="Plumbing",
            urgency="medium",
            description="Leak",
            scheduled_time=target_time,
        )

        with self.assertRaises(Exception) as raised:
            tickets_router.create_ticket(body, db=self.db, current_user=self.dispatcher)

        self.assertEqual(getattr(raised.exception, "status_code", None), 400)

    def test_create_ticket_rejects_unavailable_scheduled_slot(self):
        target_date = (datetime.now(scheduler_mod.TZ_ALMATY) + timedelta(days=1)).date()
        self.db.add(models.TechnicianSchedule(
            technician_id="tech-1",
            day_of_week=target_date.weekday(),
            start_time="09:00",
            end_time="12:00",
        ))
        self.db.commit()
        target_time = datetime(target_date.year, target_date.month, target_date.day, 10, 0)
        self._add_ticket(
            "TKT-BLOCKED-CREATE",
            1,
            datetime.now(timezone.utc),
            scheduled_time=target_time,
            assigned_to="tech-1",
        )
        body = schemas.TicketCreate(
            tenant_id=1,
            category="Plumbing",
            urgency="medium",
            description="Leak",
            scheduled_time=target_time,
            assigned_to="tech-1",
        )

        with self.assertRaises(Exception) as raised:
            tickets_router.create_ticket(body, db=self.db, current_user=self.dispatcher)

        self.assertEqual(getattr(raised.exception, "status_code", None), 400)

    def test_create_ticket_accepts_available_scheduled_slot_and_notifies(self):
        target_date = (datetime.now(scheduler_mod.TZ_ALMATY) + timedelta(days=1)).date()
        self.db.add(models.TechnicianSchedule(
            technician_id="tech-1",
            day_of_week=target_date.weekday(),
            start_time="09:00",
            end_time="12:00",
        ))
        self.db.commit()
        target_time = datetime(target_date.year, target_date.month, target_date.day, 9, 0)
        body = schemas.TicketCreate(
            tenant_id=1,
            category="Plumbing",
            urgency="medium",
            description="Leak",
            scheduled_time=target_time,
            assigned_to="tech-1",
        )

        with (
            patch("backend.src.routers.tickets.notifier.generate_technician_assignment_message", return_value="message"),
            patch("backend.src.routers.tickets.notifier.notify_technician") as notify_technician,
        ):
            result = tickets_router.create_ticket(body, db=self.db, current_user=self.dispatcher)

        ticket = self.db.query(models.Ticket).filter(models.Ticket.ticket_number == result.id).one()
        self.assertEqual(ticket.assigned_to, "tech-1")
        self.assertEqual(ticket.scheduled_time, target_time)
        notify_technician.assert_called_once_with(self.db, "tech-1", "message")

    def test_update_ticket_changes_description(self):
        self._add_ticket("TKT-DESC", 1, datetime(2026, 5, 17, tzinfo=timezone.utc))

        result = tickets_router.update_ticket(
            "TKT-DESC",
            {"description": "Updated description"},
            db=self.db,
            current_user=self.dispatcher,
        )

        self.assertEqual(result.issueDetails.description, "Updated description")
        ticket = self.db.query(models.Ticket).filter(models.Ticket.ticket_number == "TKT-DESC").one()
        self.assertEqual(ticket.description, "Updated description")

    def test_building_ticket_history_is_scoped_to_building(self):
        base = datetime(2026, 5, 17, 8, tzinfo=timezone.utc)
        self._add_ticket("TKT-A-OLD", 1, base)
        self._add_ticket("TKT-A-NEW", 1, base + timedelta(hours=1))
        self._add_ticket("TKT-B", 2, base + timedelta(hours=2))

        result = agents_router.get_building_tickets(1, current_user=self.agent, db=self.db)

        self.assertEqual([t.id for t in result], ["TKT-A-NEW", "TKT-A-OLD"])
        self.assertEqual(result[0].tenantName, "Alice Tenant")
        self.assertEqual(result[0].apartment, "10")

    def test_ticket_available_slots_are_for_assigned_technician_and_exclude_current_ticket(self):
        target_date = (datetime.now(scheduler_mod.TZ_ALMATY) + timedelta(days=1)).date()
        self.db.add_all([
            models.TechnicianSchedule(
                technician_id="tech-1",
                day_of_week=target_date.weekday(),
                start_time="09:00",
                end_time="12:00",
            ),
            models.TechnicianSchedule(
                technician_id="tech-2",
                day_of_week=target_date.weekday(),
                start_time="09:00",
                end_time="12:00",
            ),
        ])
        self.db.commit()
        current_time = datetime(target_date.year, target_date.month, target_date.day, 9, 0)
        occupied_time = datetime(target_date.year, target_date.month, target_date.day, 10, 0)
        self._add_ticket(
            "TKT-CURRENT",
            1,
            datetime.now(timezone.utc),
            scheduled_time=current_time,
            assigned_to="tech-1",
        )
        self._add_ticket(
            "TKT-OCCUPIED",
            1,
            datetime.now(timezone.utc),
            scheduled_time=occupied_time,
            assigned_to="tech-1",
        )

        result = tickets_router.get_ticket_available_slots(
            "TKT-CURRENT",
            target_date=target_date,
            db=self.db,
            current_user=self.dispatcher,
        )

        starts = {slot["start"][11:16] for slot in result}
        self.assertTrue(all(slot["technician_id"] == "tech-1" for slot in result))
        self.assertIn("09:00", starts)
        self.assertNotIn("10:00", starts)

    def test_ticket_available_slots_range_returns_next_days_for_assigned_technician(self):
        target_date = (datetime.now(scheduler_mod.TZ_ALMATY) + timedelta(days=1)).date()
        next_date = target_date + timedelta(days=1)
        self.db.add_all([
            models.TechnicianSchedule(
                technician_id="tech-1",
                day_of_week=target_date.weekday(),
                start_time="09:00",
                end_time="11:00",
            ),
            models.TechnicianSchedule(
                technician_id="tech-1",
                day_of_week=next_date.weekday(),
                start_time="14:00",
                end_time="16:00",
            ),
            models.TechnicianSchedule(
                technician_id="tech-2",
                day_of_week=target_date.weekday(),
                start_time="09:00",
                end_time="11:00",
            ),
        ])
        self.db.commit()
        self._add_ticket(
            "TKT-RANGE",
            1,
            datetime.now(timezone.utc),
            assigned_to="tech-1",
        )

        result = tickets_router.get_ticket_available_slots(
            "TKT-RANGE",
            target_date=None,
            date_from=target_date,
            days=2,
            db=self.db,
            current_user=self.dispatcher,
        )

        date_keys = {slot["start"][:10] for slot in result}
        self.assertTrue(all(slot["technician_id"] == "tech-1" for slot in result))
        self.assertIn(target_date.isoformat(), date_keys)
        self.assertIn(next_date.isoformat(), date_keys)

    def test_technician_available_slots_range_returns_selected_technician_slots(self):
        target_date = (datetime.now(scheduler_mod.TZ_ALMATY) + timedelta(days=1)).date()
        self.db.add_all([
            models.TechnicianSchedule(
                technician_id="tech-1",
                day_of_week=target_date.weekday(),
                start_time="09:00",
                end_time="11:00",
            ),
            models.TechnicianSchedule(
                technician_id="tech-2",
                day_of_week=target_date.weekday(),
                start_time="12:00",
                end_time="14:00",
            ),
        ])
        self.db.commit()

        result = technicians_router.get_technician_available_slots(
            "tech-1",
            date_from=target_date,
            days=1,
            db=self.db,
            current_user=self.dispatcher,
        )

        self.assertTrue(result)
        self.assertTrue(all(slot["technician_id"] == "tech-1" for slot in result))
        self.assertTrue(all(slot["start"].startswith(target_date.isoformat()) for slot in result))

    def test_update_ticket_rejects_schedule_change_without_assignee(self):
        target_date = (datetime.now(scheduler_mod.TZ_ALMATY) + timedelta(days=1)).date()
        target_time = datetime(target_date.year, target_date.month, target_date.day, 9, 0)
        self._add_ticket("TKT-NO-TECH", 1, datetime.now(timezone.utc))

        with self.assertRaises(Exception) as raised:
            tickets_router.update_ticket(
                "TKT-NO-TECH",
                {"scheduledDate": target_time.isoformat()},
                db=self.db,
                current_user=self.dispatcher,
            )

        self.assertEqual(getattr(raised.exception, "status_code", None), 400)

    def test_update_ticket_rejects_unavailable_slot(self):
        target_date = (datetime.now(scheduler_mod.TZ_ALMATY) + timedelta(days=1)).date()
        self.db.add(models.TechnicianSchedule(
            technician_id="tech-1",
            day_of_week=target_date.weekday(),
            start_time="09:00",
            end_time="12:00",
        ))
        self.db.commit()
        target_time = datetime(target_date.year, target_date.month, target_date.day, 10, 0)
        self._add_ticket("TKT-CHANGE", 1, datetime.now(timezone.utc), assigned_to="tech-1")
        self._add_ticket(
            "TKT-BLOCKER",
            1,
            datetime.now(timezone.utc),
            scheduled_time=target_time,
            assigned_to="tech-1",
        )

        with self.assertRaises(Exception) as raised:
            tickets_router.update_ticket(
                "TKT-CHANGE",
                {"scheduledDate": target_time.isoformat()},
                db=self.db,
                current_user=self.dispatcher,
            )

        self.assertEqual(getattr(raised.exception, "status_code", None), 400)

    def test_update_ticket_notifies_technician_on_successful_schedule_change(self):
        target_date = (datetime.now(scheduler_mod.TZ_ALMATY) + timedelta(days=1)).date()
        self.db.add(models.TechnicianSchedule(
            technician_id="tech-1",
            day_of_week=target_date.weekday(),
            start_time="09:00",
            end_time="12:00",
        ))
        self.db.commit()
        target_time = datetime(target_date.year, target_date.month, target_date.day, 9, 0)
        self._add_ticket("TKT-RESCHEDULE", 1, datetime.now(timezone.utc), assigned_to="tech-1")

        with (
            patch("backend.src.routers.tickets.notifier.generate_technician_assignment_message", return_value="message") as generate_message,
            patch("backend.src.routers.tickets.notifier.notify_technician") as notify_technician,
        ):
            result = tickets_router.update_ticket(
                "TKT-RESCHEDULE",
                {"scheduledDate": target_time.isoformat()},
                db=self.db,
                current_user=self.dispatcher,
            )

        self.assertEqual(result.scheduledDate, target_time.isoformat())
        generate_message.assert_called_once()
        notify_technician.assert_called_once_with(self.db, "tech-1", "message")

    def test_delete_ticket_removes_ticket_and_notes(self):
        ticket = self._add_ticket("TKT-DELETE", 1, datetime.now(timezone.utc))
        self.db.add(models.TicketNote(
            ticket_id=ticket.id,
            author_id="dispatcher-1",
            text="Delete this note too",
        ))
        self.db.commit()

        result = tickets_router.delete_ticket(
            "TKT-DELETE",
            db=self.db,
            current_user=self.dispatcher,
        )

        self.assertEqual(result, {"detail": "Ticket deleted"})
        self.assertEqual(self.db.query(models.Ticket).filter(models.Ticket.ticket_number == "TKT-DELETE").count(), 0)
        self.assertEqual(self.db.query(models.TicketNote).filter(models.TicketNote.ticket_id == ticket.id).count(), 0)


class CapturingNotifier:
    def __init__(self):
        self.replies = []
        self.escalations = []

    def send_reply(self, chat_id, text):
        self.replies.append((chat_id, text))

    def escalate(self, *args, **kwargs):
        self.escalations.append((args, kwargs))

    def notify_technician_assigned(self, *args, **kwargs):
        return True


class RescheduleNotificationTests(unittest.TestCase):
    def test_reschedule_tool_notifies_technician(self):
        class RescheduleScheduler:
            def __init__(self):
                self.verify_args = None
                self.reschedule_args = None

            def verify_slot_available(self, technician_id, start_iso, exclude_ticket_id=None):
                self.verify_args = (technician_id, start_iso, exclude_ticket_id)
                return True

            def reschedule_ticket(self, ticket_number, tenant_id, technician_id, new_start_iso):
                self.reschedule_args = (ticket_number, tenant_id, technician_id, new_start_iso)
                return TicketResult(
                    ticket_number=ticket_number,
                    assigned_to=technician_id,
                    description="Leak",
                    category="Plumbing",
                    urgency="medium",
                )

        class RescheduleNotifier:
            def __init__(self):
                self.notifications = []

            def notify_technician_assigned(self, **kwargs):
                self.notifications.append(kwargs)
                return True

            def send_reply(self, *args, **kwargs):
                pass

            def escalate(self, *args, **kwargs):
                pass

        tenant = TenantInfo(
            id=1,
            name="Tenant",
            phone="77700000000",
            building_name="Building",
            apartment="10",
            agent_enabled=True,
        )
        snapshot = ConversationSnapshot(
            id=1,
            tenant_id=1,
            chat_id="77700000000@c.us",
            status="open",
            state=ConversationState.managing_ticket,
            scenario=None,
            context_data={
                "offered_slots": [{
                    "technician_id": "tech-1",
                    "technician_name": "Technician One",
                    "start": "2026-05-25T09:00:00+05:00",
                    "end": "2026-05-25T10:00:00+05:00",
                }],
                "reschedule_ticket_id": 42,
                "ticket_id_map": {"TKT-RESCHEDULE": 42},
            },
            escalated_at=None,
            reopened_at=None,
        )
        ctx = ConversationContext(snapshot, tenant, "77700000000")
        scheduler = RescheduleScheduler()
        notifier = RescheduleNotifier()
        engine = AgentEngine(FakeStore(), scheduler, notifier, FailingLLM())

        result = engine._execute_tool(
            "reschedule_ticket",
            {"ticket_number": "TKT-RESCHEDULE", "slot_index": 0},
            ctx,
            [],
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(scheduler.verify_args, ("tech-1", "2026-05-25T09:00:00+05:00", 42))
        self.assertEqual(
            scheduler.reschedule_args,
            ("TKT-RESCHEDULE", 1, "tech-1", "2026-05-25T09:00:00+05:00"),
        )
        self.assertEqual(len(notifier.notifications), 1)
        notification = notifier.notifications[0]
        self.assertEqual(notification["technician_name"], "Technician One")
        self.assertEqual(notification["ticket_number"], "TKT-RESCHEDULE")
        self.assertEqual(notification["tenant"], tenant)
        self.assertEqual(notification["scheduled_time"], "2026-05-25T09:00:00+05:00")
        self.assertEqual(ctx.context_data["offered_slots"], [])


class AutoGreetingTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        self.db = session_factory()
        self.db.add(models.Tenant(
            id=1,
            name="Tenant",
            phone="77700000000",
            apartment="1",
            agent_enabled=True,
        ))
        self.db.commit()
        self.notifier = CapturingNotifier()
        self.engine = AgentEngine(
            store=SqlConversationStore(self.db),
            scheduler=None,
            notifier=self.notifier,
            llm=FailingLLM(),
        )

    def tearDown(self):
        self.db.close()

    def test_greeting_is_saved_and_sent_once_for_new_open_conversation(self):
        tenant, snapshot, chat_id = self.engine.save_incoming_message("77700000000", "Hello")

        self.assertIsNotNone(tenant)
        self.assertEqual(chat_id, "77700000000@c.us")
        self.assertIn("Алмат", AUTO_GREETING_TEXT)
        self.assertTrue(getattr(snapshot, "_greeting_sent_now", False))
        self.assertEqual(self.notifier.replies, [("77700000000@c.us", AUTO_GREETING_TEXT)])
        messages = self.db.query(models.Message).order_by(models.Message.id.asc()).all()
        self.assertEqual([m.sender for m in messages], [models.MessageSenderEnum.ai, models.MessageSenderEnum.tenant])
        self.assertEqual(messages[0].content, AUTO_GREETING_TEXT)

        self.engine.save_incoming_message("77700000000", "Second")

        self.assertEqual(len(self.notifier.replies), 1)
        ai_count = self.db.query(models.Message).filter(models.Message.sender == models.MessageSenderEnum.ai).count()
        self.assertEqual(ai_count, 1)

    def test_greeting_is_sent_after_reopen_once(self):
        self.engine.save_incoming_message("77700000000", "Hello")
        conversation = self.db.query(models.Conversation).one()
        conversation.status = models.ConversationStatusEnum.closed
        self.db.commit()

        tenant, snapshot, _ = self.engine.save_incoming_message("77700000000", "Again")

        self.assertIsNotNone(tenant)
        self.assertTrue(getattr(snapshot, "_greeting_sent_now", False))
        self.assertEqual(len(self.notifier.replies), 2)
        ai_count = self.db.query(models.Message).filter(models.Message.sender == models.MessageSenderEnum.ai).count()
        self.assertEqual(ai_count, 2)


if __name__ == "__main__":
    unittest.main()
