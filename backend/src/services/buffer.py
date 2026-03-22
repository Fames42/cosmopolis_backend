"""Message aggregation buffer — collects rapid-fire messages and processes them as one."""

import asyncio
import logging
from dataclasses import dataclass, field

from ..database import SessionLocal
from ..agent.types import AgentResult
from ..agent.context import ConversationContext
from .adapters import create_agent_engine
from .notifier import send_whatsapp_reply

logger = logging.getLogger("uvicorn.error")

BUFFER_DELAY_SECONDS = 5


@dataclass
class BufferedMessage:
    phone: str
    content: str
    image_base64: str | None = None


@dataclass
class ChatBuffer:
    messages: list[BufferedMessage] = field(default_factory=list)
    timer_task: asyncio.Task | None = None
    processing_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    result_futures: list[asyncio.Future] = field(default_factory=list)


class MessageBuffer:
    def __init__(self) -> None:
        self._buffers: dict[str, ChatBuffer] = {}

    def _get_buffer(self, chat_id: str) -> ChatBuffer:
        if chat_id not in self._buffers:
            self._buffers[chat_id] = ChatBuffer()
        return self._buffers[chat_id]

    async def add_message(
        self, chat_id: str, phone: str, content: str, image_base64: str | None = None,
    ) -> None:
        """Buffer a message for fire-and-forget processing (greenapi webhook)."""
        buf = self._get_buffer(chat_id)

        # Save to DB immediately
        db = SessionLocal()
        try:
            engine = create_agent_engine(db)
            tenant, snapshot, _ = engine.save_incoming_message(phone, content, image_base64=image_base64)
            if not tenant:
                logger.info("Ignoring message from non-tenant %s", phone)
                return
        finally:
            db.close()

        buf.messages.append(BufferedMessage(phone=phone, content=content, image_base64=image_base64))
        await self._schedule_flush(chat_id)

    async def add_message_and_wait(
        self, chat_id: str, phone: str, content: str, image_base64: str | None = None,
    ) -> tuple[str, str, AgentResult | None]:
        """Buffer a message and wait for the aggregated result (test endpoint)."""
        buf = self._get_buffer(chat_id)

        # Save to DB immediately
        db = SessionLocal()
        try:
            engine = create_agent_engine(db)
            tenant, snapshot, _ = engine.save_incoming_message(phone, content, image_base64=image_base64)
            if not tenant:
                return (
                    "Извините, ваш номер не найден в системе. Обратитесь в управляющую компанию для регистрации.",
                    "unknown_tenant",
                    None,
                )
        finally:
            db.close()

        buf.messages.append(BufferedMessage(phone=phone, content=content, image_base64=image_base64))

        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[str, str, AgentResult | None]] = loop.create_future()
        buf.result_futures.append(future)

        await self._schedule_flush(chat_id)

        return await future

    async def _schedule_flush(self, chat_id: str) -> None:
        buf = self._get_buffer(chat_id)
        if buf.timer_task and not buf.timer_task.done():
            buf.timer_task.cancel()
        buf.timer_task = asyncio.create_task(self._delayed_flush(chat_id))

    async def _delayed_flush(self, chat_id: str) -> None:
        try:
            await asyncio.sleep(BUFFER_DELAY_SECONDS)
            await self._flush(chat_id)
        except asyncio.CancelledError:
            pass

    async def _flush(self, chat_id: str) -> None:
        buf = self._get_buffer(chat_id)

        async with buf.processing_lock:
            messages = buf.messages[:]
            futures = buf.result_futures[:]
            buf.messages.clear()
            buf.result_futures.clear()
            buf.timer_task = None

            if not messages:
                return

            phone = messages[0].phone

            result = await asyncio.get_running_loop().run_in_executor(
                None, self._process_sync, phone, chat_id,
            )

            for fut in futures:
                if not fut.done():
                    fut.set_result(result)

            reply = result[0]
            state = result[1]
            if reply and state not in ("unknown_tenant", "agent_disabled"):
                send_whatsapp_reply(chat_id, reply)

        if buf.messages:
            await self._schedule_flush(chat_id)
        else:
            self._buffers.pop(chat_id, None)

    @staticmethod
    def _process_sync(
        phone: str, chat_id: str,
    ) -> tuple[str, str, AgentResult | None]:
        """Synchronous processing in executor — creates isolated engine + context."""
        db = SessionLocal()
        try:
            engine = create_agent_engine(db)
            tenant = engine.store.find_tenant_by_phone(phone)
            if not tenant:
                return (
                    "Извините, ваш номер не найден в системе. Обратитесь в управляющую компанию для регистрации.",
                    "unknown_tenant",
                    None,
                )

            snapshot = engine.store.get_or_create_conversation(tenant.id, chat_id)
            ctx = ConversationContext(snapshot, tenant, phone)
            return engine.process_conversation(ctx)
        finally:
            db.close()


# Module-level singleton
message_buffer = MessageBuffer()
