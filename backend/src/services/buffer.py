"""Message aggregation buffer — collects rapid-fire messages and processes them as one."""

import asyncio
import logging
from dataclasses import dataclass, field

from ..database import SessionLocal
from ..schemas import AgentResponse
from .orchestrator import save_incoming_message, process_conversation
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
            tenant, conv, _ = save_incoming_message(db, phone, content, image_base64=image_base64)
            if not tenant:
                logger.info("Ignoring message from non-tenant %s", phone)
                return
        finally:
            db.close()

        buf.messages.append(BufferedMessage(phone=phone, content=content, image_base64=image_base64))
        await self._schedule_flush(chat_id)

    async def add_message_and_wait(
        self, chat_id: str, phone: str, content: str, image_base64: str | None = None,
    ) -> tuple[str, str, AgentResponse | None]:
        """Buffer a message and wait for the aggregated result (test endpoint)."""
        buf = self._get_buffer(chat_id)

        # Save to DB immediately
        db = SessionLocal()
        try:
            tenant, conv, _ = save_incoming_message(db, phone, content, image_base64=image_base64)
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
        future: asyncio.Future[tuple[str, str, AgentResponse | None]] = loop.create_future()
        buf.result_futures.append(future)

        await self._schedule_flush(chat_id)

        return await future

    async def _schedule_flush(self, chat_id: str) -> None:
        """Cancel any pending timer and schedule a new flush after BUFFER_DELAY_SECONDS."""
        buf = self._get_buffer(chat_id)

        if buf.timer_task and not buf.timer_task.done():
            buf.timer_task.cancel()

        buf.timer_task = asyncio.create_task(self._delayed_flush(chat_id))

    async def _delayed_flush(self, chat_id: str) -> None:
        """Wait for the buffer delay then flush."""
        try:
            await asyncio.sleep(BUFFER_DELAY_SECONDS)
            await self._flush(chat_id)
        except asyncio.CancelledError:
            pass

    async def _flush(self, chat_id: str) -> None:
        """Process all buffered messages for a chat as a single aggregated request."""
        buf = self._get_buffer(chat_id)

        # Wait for any in-progress processing to finish
        async with buf.processing_lock:
            messages = buf.messages[:]
            futures = buf.result_futures[:]
            buf.messages.clear()
            buf.result_futures.clear()
            buf.timer_task = None

            if not messages:
                return

            phone = messages[0].phone

            # Run DB operations in a thread to avoid blocking the event loop
            result = await asyncio.get_running_loop().run_in_executor(
                None, self._process_sync, phone, chat_id,
            )

            # Resolve all waiting futures
            for fut in futures:
                if not fut.done():
                    fut.set_result(result)

            # Send WhatsApp reply for greenapi messages
            reply = result[0]
            state = result[1]
            if reply and state != "unknown_tenant":
                send_whatsapp_reply(chat_id, reply)

        # Check if new messages arrived while we were processing
        if buf.messages:
            await self._schedule_flush(chat_id)
        else:
            # Clean up empty buffer
            self._buffers.pop(chat_id, None)

    @staticmethod
    def _process_sync(
        phone: str, chat_id: str,
    ) -> tuple[str, str, AgentResponse | None]:
        """Synchronous DB + orchestrator processing, run in executor."""
        from .orchestrator import _find_tenant, _get_or_create_conversation

        db = SessionLocal()
        try:
            tenant = _find_tenant(db, phone)
            if not tenant:
                return (
                    "Извините, ваш номер не найден в системе. Обратитесь в управляющую компанию для регистрации.",
                    "unknown_tenant",
                    None,
                )

            conv = _get_or_create_conversation(db, tenant, chat_id)
            return process_conversation(db, conv, tenant, phone)
        finally:
            db.close()


# Module-level singleton
message_buffer = MessageBuffer()
