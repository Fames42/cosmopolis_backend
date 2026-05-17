import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.src import models
from backend.src.database import Base
from backend.src.agent.context import ConversationContext
from backend.src.agent.engine import AgentEngine
from backend.src.agent.types import ConversationSnapshot, ConversationState, TenantInfo
from backend.src.routers.webhook import greenapi_webhook
from backend.src.services.adapters import SqlConversationStore
from backend.src.services.buffer import BufferedMessage, MessageBuffer
from backend.src.routers.technicians import require_self_technician


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


if __name__ == "__main__":
    unittest.main()
