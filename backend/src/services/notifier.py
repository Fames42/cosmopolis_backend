import os
import logging

import httpx

logger = logging.getLogger("uvicorn.error")

_ID_INSTANCE = os.getenv("ID_INSTANCE", "")
_API_TOKEN = os.getenv("API_TOKEN_INSTANCE", "")
_GROUP_CHAT_ID = os.getenv("ALERT_GROUP_CHAT_ID", "")
_API_URL = "https://api.green-api.com"


def send_escalation_alert(
    tenant_name: str,
    tenant_phone: str,
    building_name: str,
    apartment: str,
    last_message: str,
) -> None:
    """Send a WhatsApp alert to the dispatcher group chat. Fire-and-forget."""
    if not all([_ID_INSTANCE, _API_TOKEN, _GROUP_CHAT_ID]):
        logger.warning("Escalation alert not sent: missing ID_INSTANCE, API_TOKEN_INSTANCE, or ALERT_GROUP_CHAT_ID")
        return

    text = (
        "⚠️ *Требуется оператор*\n\n"
        f"*Жилец:* {tenant_name}\n"
        f"*Телефон:* {tenant_phone}\n"
        f"*Здание:* {building_name}, кв. {apartment}\n\n"
        f"*Последнее сообщение:*\n{last_message}"
    )

    url = f"{_API_URL}/waInstance{_ID_INSTANCE}/sendMessage/{_API_TOKEN}"
    payload = {"chatId": _GROUP_CHAT_ID, "message": text}

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
        logger.info("Escalation alert sent to group %s", _GROUP_CHAT_ID)
    except Exception:
        logger.exception("Failed to send escalation alert")


def send_whatsapp_reply(chat_id: str, text: str) -> None:
    """Send a WhatsApp message to a chat. Fire-and-forget."""
    if not all([_ID_INSTANCE, _API_TOKEN]):
        logger.warning("WhatsApp reply not sent: missing ID_INSTANCE or API_TOKEN_INSTANCE")
        return

    url = f"{_API_URL}/waInstance{_ID_INSTANCE}/sendMessage/{_API_TOKEN}"
    payload = {"chatId": chat_id, "message": text}

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
        logger.info("WhatsApp reply sent to %s", chat_id)
    except Exception:
        logger.exception("Failed to send WhatsApp reply to %s", chat_id)
