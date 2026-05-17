import base64
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter

from ..database import SessionLocal
from ..schemas import TestMessageRequest, TestMessageResponse
from ..services.adapters import create_agent_engine
from ..services.buffer import message_buffer

logger = logging.getLogger("uvicorn.error")

router = APIRouter()
OPERATOR_PAUSE_SECONDS = 60 * 60


def _download_image_as_base64(download_url: str) -> str | None:
    """Download an image from a URL and return it as a base64-encoded data URI."""
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(download_url)
            resp.raise_for_status()
        content_type = resp.headers.get("content-type", "image/jpeg")
        b64 = base64.b64encode(resp.content).decode("utf-8")
        return f"data:{content_type};base64,{b64}"
    except Exception:
        logger.exception("Failed to download image from %s", download_url)
        return None


def _phone_to_chat_id(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    return f"{digits}@c.us"


def _extract_message_text(message_data: dict) -> str:
    msg_type = message_data.get("typeMessage", "")
    if msg_type == "textMessage":
        return message_data.get("textMessageData", {}).get("textMessage", "")
    if msg_type == "extendedTextMessage":
        return message_data.get("extendedTextMessageData", {}).get("text", "")
    if msg_type == "imageMessage":
        return message_data.get("fileMessageData", {}).get("caption", "") or "[Фото]"
    if msg_type:
        return f"[{msg_type}]"
    return "[operator message]"


def _pause_operator_chat(phone: str, content: str, reason: str) -> bool:
    paused_until = datetime.now(timezone.utc) + timedelta(seconds=OPERATOR_PAUSE_SECONDS)
    db = SessionLocal()
    try:
        engine = create_agent_engine(db)
        return engine.store.pause_for_operator(
            phone=phone,
            content=content,
            paused_until=paused_until,
            reason=reason,
        )
    finally:
        db.close()


@router.post("/test", response_model=TestMessageResponse)
async def test_webhook(req: TestMessageRequest):
    """Test endpoint to simulate an incoming WhatsApp message.

    Messages are buffered for 15 seconds. If multiple messages arrive from the
    same phone within that window, they are aggregated and processed as one.
    The response is returned after the buffer flushes.
    """
    chat_id = _phone_to_chat_id(req.phone)

    reply, state, agent_resp = await message_buffer.add_message_and_wait(
        chat_id=chat_id,
        phone=req.phone,
        content=req.message,
    )

    return TestMessageResponse(
        reply=reply,
        state=state,
        agent_response=agent_resp,
    )


@router.post("/greenapi")
async def greenapi_webhook(request_body: dict):
    """Receive WhatsApp tenant/operator events from Green API webhook.

    Incoming tenant messages are buffered and processed by the agent. Outgoing
    phone messages are treated as operator responses and pause the agent.
    """
    webhook_type = request_body.get("typeWebhook")

    if webhook_type == "outgoingAPIMessageReceived":
        return {"status": "ignored", "type": webhook_type}

    if webhook_type not in ("incomingMessageReceived", "outgoingMessageReceived"):
        return {"status": "ignored", "type": webhook_type}

    sender_data = request_body.get("senderData", {})
    message_data = request_body.get("messageData", {})

    chat_id = sender_data.get("chatId", "")
    # Skip group messages — only handle personal chats
    if not chat_id.endswith("@c.us"):
        return {"status": "ignored", "reason": "group_chat"}

    # Extract phone number from chat_id (e.g. "77762113673@c.us" → "77762113673")
    phone = chat_id.replace("@c.us", "")

    if webhook_type == "outgoingMessageReceived":
        text = _extract_message_text(message_data)
        paused = _pause_operator_chat(phone, text, webhook_type)
        if not paused:
            logger.info("Ignoring operator message from non-tenant %s", phone)
            return {"status": "ignored", "reason": "unknown_tenant"}
        logger.info("Operator message paused agent for %s until +%ds", phone, OPERATOR_PAUSE_SECONDS)
        return {"status": "ok", "operator_paused": True, "pause_seconds": OPERATOR_PAUSE_SECONDS}

    # Extract message text and optional image
    text = ""
    image_base64 = None
    msg_type = message_data.get("typeMessage", "")

    if msg_type == "textMessage":
        text = message_data.get("textMessageData", {}).get("textMessage", "")
    elif msg_type == "extendedTextMessage":
        text = message_data.get("extendedTextMessageData", {}).get("text", "")
    elif msg_type == "imageMessage":
        file_data = message_data.get("fileMessageData", {})
        text = file_data.get("caption", "")
        download_url = file_data.get("downloadUrl", "")
        if download_url:
            image_base64 = _download_image_as_base64(download_url)
            logger.info("Image downloaded from WhatsApp for %s", phone)

    if not text and not image_base64:
        return {"status": "ignored", "reason": "no_text"}

    logger.info("Incoming WhatsApp from %s: %s%s", phone, text[:100] if text else "[image]",
                " +image" if image_base64 else "")

    await message_buffer.add_message(
        chat_id=chat_id,
        phone=phone,
        content=text or "[Фото]",
        image_base64=image_base64,
    )

    return {"status": "ok", "buffered": True}
