import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import TypeAdapter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.src import models, schemas
from backend.src.database import Base
from backend.src.agent.context import ConversationContext
from backend.src.agent.engine import AUTO_GREETING_TEXT, AgentEngine
from backend.src.agent.types import (
    ConversationSnapshot,
    ConversationState,
    HistoryMessage,
    TenantInfo,
    TicketCancellationResult,
    TicketResult,
)
from backend.src.routers.webhook import greenapi_webhook
from backend.src.services.adapters import SqlConversationStore, SqlSchedulingService, WhatsAppNotificationService
from backend.src.services.buffer import BufferedMessage, MessageBuffer
from backend.src.services import images as image_service
from backend.src.services import reminders as reminder_service
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

    def test_conversation_response_allows_legacy_null_tenant_id(self):
        conversation = models.Conversation(
            id=99,
            tenant_id=None,
            whatsapp_chat_id="77010000000@c.us",
            status=models.ConversationStatusEnum.open,
            state=models.ConversationStateEnum.new_conversation,
            created_at=datetime.now(timezone.utc),
        )

        result = TypeAdapter(list[schemas.ConversationResponse]).validate_python(
            [conversation],
            from_attributes=True,
        )

        self.assertIsNone(result[0].tenant_id)


class ImageHelperTests(unittest.TestCase):
    def test_bytes_to_data_uri_accepts_supported_images(self):
        result = image_service.bytes_to_data_uri(b"image-bytes", "image/jpeg")

        self.assertEqual(result, "data:image/jpeg;base64,aW1hZ2UtYnl0ZXM=")

    def test_bytes_to_data_uri_rejects_invalid_content_type(self):
        with self.assertRaises(image_service.ImageValidationError):
            image_service.bytes_to_data_uri(b"not-image", "text/plain")

    def test_bytes_to_data_uri_rejects_oversized_images(self):
        with self.assertRaises(image_service.ImageValidationError):
            image_service.bytes_to_data_uri(b"x" * (image_service.MAX_IMAGE_BYTES + 1), "image/png")


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

    async def test_incoming_image_message_uses_shared_image_helper(self):
        body = {
            "typeWebhook": "incomingMessageReceived",
            "senderData": {"chatId": "77700000000@c.us"},
            "messageData": {
                "typeMessage": "imageMessage",
                "fileMessageData": {
                    "caption": "Leak photo",
                    "downloadUrl": "https://example.test/photo.jpg",
                },
            },
        }

        with (
            patch(
                "backend.src.routers.webhook.images.download_url_to_data_uri",
                return_value="data:image/jpeg;base64,abc",
            ) as download,
            patch("backend.src.routers.webhook.message_buffer.add_message", new_callable=AsyncMock) as add_message,
        ):
            response = await greenapi_webhook(body)

        self.assertEqual(response["status"], "ok")
        download.assert_called_once_with("https://example.test/photo.jpg")
        add_message.assert_awaited_once_with(
            chat_id="77700000000@c.us",
            phone="77700000000",
            content="Leak photo",
            image_base64="data:image/jpeg;base64,abc",
        )


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

        with patch("backend.src.routers.tickets.notifier.notify_technician_lifecycle") as notify_lifecycle:
            result = tickets_router.create_ticket(body, db=self.db, current_user=self.dispatcher)

        ticket = self.db.query(models.Ticket).filter(models.Ticket.ticket_number == result.id).one()
        self.assertIsNone(ticket.availability_time)
        self.assertEqual(ticket.assigned_to, "tech-1")
        self.assertEqual(ticket.status, models.TicketStatusEnum.assigned)
        notify_lifecycle.assert_called_once()
        self.assertEqual(notify_lifecycle.call_args.kwargs["technician_id"], "tech-1")
        self.assertEqual(notify_lifecycle.call_args.kwargs["action"], "assigned")

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

        with patch("backend.src.routers.tickets.notifier.notify_technician_lifecycle") as notify_lifecycle:
            result = tickets_router.create_ticket(body, db=self.db, current_user=self.dispatcher)

        ticket = self.db.query(models.Ticket).filter(models.Ticket.ticket_number == result.id).one()
        self.assertEqual(ticket.assigned_to, "tech-1")
        self.assertEqual(ticket.scheduled_time, target_time)
        notify_lifecycle.assert_called_once()
        self.assertEqual(notify_lifecycle.call_args.kwargs["technician_id"], "tech-1")
        self.assertEqual(notify_lifecycle.call_args.kwargs["action"], "assigned")

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

    def test_technician_workload_and_active_count_exclude_cancelled_assigned_tickets(self):
        scheduled_time = datetime(2026, 6, 8, 11, 0)
        self._add_ticket(
            "TKT-ACTIVE-WORKLOAD",
            1,
            datetime.now(timezone.utc),
            scheduled_time=scheduled_time,
            assigned_to="tech-1",
        )
        cancelled = self._add_ticket(
            "TKT-CANCELLED-WORKLOAD",
            1,
            datetime.now(timezone.utc),
            scheduled_time=scheduled_time + timedelta(hours=1),
            assigned_to="tech-1",
        )
        cancelled.status = models.TicketStatusEnum.cancelled
        self.db.commit()

        workload = technicians_router.get_technician_workload(
            "tech-1",
            date_from=None,
            date_to=None,
            db=self.db,
            current_user=self.dispatcher,
        )
        techs = technicians_router.get_technicians(db=self.db, current_user=self.dispatcher)

        self.assertEqual([ticket.ticket_number for ticket in workload.tickets], ["TKT-ACTIVE-WORKLOAD"])
        tech = next(item for item in techs if item["id"] == "tech-1")
        self.assertEqual(tech["activeTickets"], 1)

    def test_append_ticket_photo_data_urls_preserves_existing_photos(self):
        ticket = self._add_ticket("TKT-PHOTO-UPLOAD", 1, datetime.now(timezone.utc))
        ticket.photo_urls = ["data:image/jpeg;base64,old"]
        self.db.commit()

        result = tickets_router.append_ticket_photo_data_urls(
            self.db,
            ticket,
            ["data:image/png;base64,bmV3LXBob3Rv"],
        )

        self.assertEqual(result, [
            "data:image/jpeg;base64,old",
            "data:image/png;base64,bmV3LXBob3Rv",
        ])
        self.db.refresh(ticket)
        self.assertEqual(ticket.photo_urls, result)

    def test_reminder_due_time_uses_visit_time_cutoff(self):
        before_cutoff = reminder_service.compute_ticket_reminder_due_at(datetime(2026, 6, 8, 13, 59))
        at_cutoff = reminder_service.compute_ticket_reminder_due_at(datetime(2026, 6, 8, 14, 0))

        self.assertEqual(before_cutoff.isoformat(), "2026-06-07T21:00:00+05:00")
        self.assertEqual(at_cutoff.isoformat(), "2026-06-08T09:00:00+05:00")

    def test_process_due_ticket_reminders_sends_once_and_resends_after_reschedule(self):
        scheduled_time = datetime(2026, 6, 8, 15, 0)
        ticket = self._add_ticket(
            "TKT-REMINDER",
            1,
            datetime.now(timezone.utc),
            scheduled_time=scheduled_time,
            assigned_to="tech-1",
        )
        now = datetime(2026, 6, 8, 9, 1, tzinfo=scheduler_mod.TZ_ALMATY)

        with (
            patch("backend.src.services.reminders.notifier.notify_tenant_ticket_reminder") as tenant_notify,
            patch("backend.src.services.reminders.notifier.notify_technician_lifecycle") as tech_notify,
        ):
            sent_count = reminder_service.process_due_ticket_reminders(self.db, now=now)
            second_count = reminder_service.process_due_ticket_reminders(self.db, now=now)

            ticket.scheduled_time = datetime(2026, 6, 9, 15, 0)
            self.db.commit()
            third_count = reminder_service.process_due_ticket_reminders(
                self.db,
                now=datetime(2026, 6, 9, 9, 1, tzinfo=scheduler_mod.TZ_ALMATY),
            )

        self.assertEqual(sent_count, 1)
        self.assertEqual(second_count, 0)
        self.assertEqual(third_count, 1)
        self.assertEqual(tenant_notify.call_count, 2)
        self.assertEqual(tech_notify.call_count, 2)
        self.db.refresh(ticket)
        self.assertEqual(ticket.reminder_state["sent_for"], "2026-06-09T15:00:00")
        self.assertEqual(tech_notify.call_args.kwargs["action"], "reminder")

    def test_process_due_ticket_reminders_skips_inactive_and_invalid_tickets(self):
        visit_time = datetime(2026, 6, 8, 15, 0)
        cancelled = self._add_ticket(
            "TKT-REMINDER-CANCELLED",
            1,
            datetime.now(timezone.utc),
            scheduled_time=visit_time,
            assigned_to="tech-1",
        )
        cancelled.status = models.TicketStatusEnum.cancelled
        done = self._add_ticket(
            "TKT-REMINDER-DONE",
            1,
            datetime.now(timezone.utc),
            scheduled_time=visit_time,
            assigned_to="tech-1",
        )
        done.status = models.TicketStatusEnum.done
        self._add_ticket("TKT-REMINDER-UNASSIGNED", 1, datetime.now(timezone.utc), scheduled_time=visit_time)
        self._add_ticket("TKT-REMINDER-UNSCHEDULED", 1, datetime.now(timezone.utc), assigned_to="tech-1")
        self._add_ticket(
            "TKT-REMINDER-PAST",
            1,
            datetime.now(timezone.utc),
            scheduled_time=datetime(2026, 6, 7, 15, 0),
            assigned_to="tech-1",
        )
        self.db.commit()

        with (
            patch("backend.src.services.reminders.notifier.notify_tenant_ticket_reminder") as tenant_notify,
            patch("backend.src.services.reminders.notifier.notify_technician_lifecycle") as tech_notify,
        ):
            sent_count = reminder_service.process_due_ticket_reminders(
                self.db,
                now=datetime(2026, 6, 8, 9, 1, tzinfo=scheduler_mod.TZ_ALMATY),
            )

        self.assertEqual(sent_count, 0)
        tenant_notify.assert_not_called()
        tech_notify.assert_not_called()

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

        with patch("backend.src.routers.tickets.notifier.notify_technician_lifecycle") as notify_lifecycle:
            result = tickets_router.update_ticket(
                "TKT-RESCHEDULE",
                {"scheduledDate": target_time.isoformat()},
                db=self.db,
                current_user=self.dispatcher,
            )

        self.assertEqual(result.scheduledDate, target_time.isoformat())
        notify_lifecycle.assert_called_once()
        self.assertEqual(notify_lifecycle.call_args.kwargs["technician_id"], "tech-1")
        self.assertEqual(notify_lifecycle.call_args.kwargs["action"], "rescheduled")

    def test_delete_ticket_removes_ticket_and_notes(self):
        ticket = self._add_ticket("TKT-DELETE", 1, datetime.now(timezone.utc))
        self.db.add(models.TicketNote(
            ticket_id=ticket.id,
            author_id="dispatcher-1",
            text="Delete this note too",
        ))
        self.db.commit()

        with patch("backend.src.routers.tickets.notifier.notify_technician_lifecycle") as notify_lifecycle:
            result = tickets_router.delete_ticket(
                "TKT-DELETE",
                db=self.db,
                current_user=self.dispatcher,
            )

        self.assertEqual(result, {"detail": "Ticket deleted"})
        self.assertEqual(self.db.query(models.Ticket).filter(models.Ticket.ticket_number == "TKT-DELETE").count(), 0)
        self.assertEqual(self.db.query(models.TicketNote).filter(models.TicketNote.ticket_id == ticket.id).count(), 0)
        notify_lifecycle.assert_not_called()

    def test_delete_ticket_notifies_assigned_technician_before_delete(self):
        scheduled_time = datetime(2026, 6, 8, 11, 0)
        self._add_ticket(
            "TKT-DELETE-ASSIGNED",
            1,
            datetime.now(timezone.utc),
            scheduled_time=scheduled_time,
            assigned_to="tech-1",
        )

        with patch("backend.src.routers.tickets.notifier.notify_technician_lifecycle") as notify_lifecycle:
            result = tickets_router.delete_ticket(
                "TKT-DELETE-ASSIGNED",
                db=self.db,
                current_user=self.dispatcher,
            )

        self.assertEqual(result, {"detail": "Ticket deleted"})
        notify_lifecycle.assert_called_once()
        kwargs = notify_lifecycle.call_args.kwargs
        self.assertEqual(kwargs["technician_id"], "tech-1")
        self.assertEqual(kwargs["action"], "deleted")
        self.assertEqual(kwargs["scheduled_time"], scheduled_time)

    def test_update_ticket_cancelled_clears_assignment_schedule_and_notifies(self):
        scheduled_time = datetime(2026, 6, 8, 11, 0)
        self._add_ticket(
            "TKT-CANCEL-DISPATCHER",
            1,
            datetime.now(timezone.utc),
            scheduled_time=scheduled_time,
            assigned_to="tech-1",
        )

        with patch("backend.src.routers.tickets.notifier.notify_technician_lifecycle") as notify_lifecycle:
            result = tickets_router.update_ticket(
                "TKT-CANCEL-DISPATCHER",
                {"status": "cancelled"},
                db=self.db,
                current_user=self.dispatcher,
            )

        ticket = self.db.query(models.Ticket).filter(models.Ticket.ticket_number == "TKT-CANCEL-DISPATCHER").one()
        self.assertEqual(ticket.status, models.TicketStatusEnum.cancelled)
        self.assertIsNone(ticket.assigned_to)
        self.assertIsNone(ticket.scheduled_time)
        self.assertIsNone(result.assignedTech)
        self.assertIsNone(result.scheduledDate)
        notify_lifecycle.assert_called_once()
        kwargs = notify_lifecycle.call_args.kwargs
        self.assertEqual(kwargs["technician_id"], "tech-1")
        self.assertEqual(kwargs["action"], "cancelled")
        self.assertEqual(kwargs["scheduled_time"], scheduled_time)


class CapturingNotifier:
    def __init__(self):
        self.replies = []
        self.escalations = []
        self.notifications = []

    def send_reply(self, chat_id, text):
        self.replies.append((chat_id, text))

    def escalate(self, *args, **kwargs):
        self.escalations.append((args, kwargs))

    def notify_technician_assigned(self, *args, **kwargs):
        return True

    def notify_technician_lifecycle(self, *args, **kwargs):
        self.notifications.append((args, kwargs))
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

            def notify_technician_lifecycle(self, **kwargs):
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
        self.assertEqual(notification["technician_id"], "tech-1")
        self.assertEqual(notification["action"], "rescheduled")
        self.assertEqual(notification["ticket_number"], "TKT-RESCHEDULE")
        self.assertEqual(notification["tenant"], tenant)
        self.assertEqual(notification["scheduled_time"], "2026-05-25T09:00:00+05:00")
        self.assertEqual(ctx.context_data["offered_slots"], [])


class BotCancellationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        self.db = session_factory()
        self.db.add_all([
            models.User(
                id="tech-1",
                name="Technician One",
                email="tech@example.com",
                phone="+77770000001",
                password_hash="x",
                role=models.RoleEnum.technician,
            ),
            models.Building(id=1, name="7C", address="Main"),
            models.Tenant(id=1, name="Tenant", phone="77471046034", apartment="10", building_id=1, agent_enabled=True),
        ])
        self.scheduled_time = datetime(2026, 6, 8, 11, 0)
        self.ticket = models.Ticket(
            ticket_number="TKT-AF971FA6",
            tenant_id=1,
            category="Plumbing",
            urgency="LOW",
            description="Leak",
            status=models.TicketStatusEnum.assigned,
            assigned_to="tech-1",
            scheduled_time=self.scheduled_time,
        )
        self.db.add(self.ticket)
        self.db.commit()
        self.db.refresh(self.ticket)

    def tearDown(self):
        self.db.close()

    def test_cancel_ticket_clears_assignment_schedule_and_notifies_technician(self):
        tenant = TenantInfo(
            id=1,
            name="Tenant",
            phone="77471046034",
            building_name="7C",
            apartment="10",
            agent_enabled=True,
        )
        snapshot = ConversationSnapshot(
            id=1,
            tenant_id=1,
            chat_id="77471046034@c.us",
            status="open",
            state=ConversationState.managing_ticket,
            scenario=None,
            context_data={},
            escalated_at=None,
            reopened_at=None,
        )
        ctx = ConversationContext(snapshot, tenant, "77471046034")
        notifier = CapturingNotifier()
        engine = AgentEngine(FakeStore(), SqlSchedulingService(self.db), notifier, FailingLLM())

        result = engine._execute_tool(
            "cancel_ticket",
            {"ticket_number": "TKT-AF971FA6"},
            ctx,
            [],
        )

        self.assertEqual(result["status"], "ok")
        self.db.refresh(self.ticket)
        self.assertEqual(self.ticket.status, models.TicketStatusEnum.cancelled)
        self.assertIsNone(self.ticket.assigned_to)
        self.assertIsNone(self.ticket.scheduled_time)
        self.assertEqual(len(notifier.notifications), 1)
        _, kwargs = notifier.notifications[0]
        self.assertEqual(kwargs["technician_id"], "tech-1")
        self.assertEqual(kwargs["action"], "cancelled")
        self.assertEqual(kwargs["ticket_number"], "TKT-AF971FA6")
        self.assertEqual(kwargs["scheduled_time"], self.scheduled_time.isoformat())
        self.assertIn("Клиент отменил заявку", kwargs["reason"])


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
