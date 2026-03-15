import os
import re
import logging

import httpx
from sqlalchemy.orm import Session

from .. import models

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


def _normalize_phone(phone: str) -> str:
    """Strip non-digits and normalise KZ numbers (leading 8 → 7)."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return digits


def _format_phone(phone: str) -> str:
    """Format phone as +7XXXXXXXXXX for display."""
    digits = _normalize_phone(phone)
    return f"+{digits}" if digits else phone


def generate_escalation_message(
    tenant_name: str,
    tenant_phone: str,
    building_name: str,
    apartment: str,
    history: list[dict[str, str]],
) -> str:
    """Generate a dispatcher notification via GPT. Falls back to hardcoded template."""
    from .classifier import _get_client, _load_prompt

    display_phone = _format_phone(tenant_phone)

    fallback = (
        "⚠️ *Требуется оператор*\n\n"
        f"*Жилец:* {tenant_name}\n"
        f"*Телефон:* {display_phone}\n"
        f"*Здание:* {building_name}, кв. {apartment}\n\n"
        f"*История:*\n" + "\n".join(
            f"{'Жилец' if m['role'] == 'tenant' else 'Бот'}: {m['content']}"
            for m in history[-5:]
        )
    )

    try:
        client = _get_client()
        prompt = _load_prompt("escalation")
        if not prompt:
            return fallback

        user_content = (
            f"TENANT DETAILS:\n"
            f"Name: {tenant_name}\n"
            f"Phone: {display_phone}\n"
            f"Building: {building_name}\n"
            f"Apartment: {apartment}\n\n"
            f"CONVERSATION HISTORY:\n"
            + "\n".join(
                f"{'Tenant' if m['role'] == 'tenant' else 'AI'}: {m['content']}"
                for m in history[-10:]
            )
        )

        response = client.chat.completions.create(
            model="gpt-5.4",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_completion_tokens=300,
        )
        return response.choices[0].message.content.strip()

    except Exception:
        logger.exception("Failed to generate escalation message via GPT, using fallback")
        return fallback


def notify_dispatchers(db: Session, message: str) -> None:
    """Send a WhatsApp message to all dispatchers who have a phone number."""
    dispatchers = (
        db.query(models.User)
        .filter(
            models.User.role == models.RoleEnum.dispatcher,
            models.User.phone.isnot(None),
            models.User.phone != "",
        )
        .all()
    )

    for dispatcher in dispatchers:
        digits = _normalize_phone(dispatcher.phone)
        if not digits:
            continue
        chat_id = f"{digits}@c.us"
        logger.info("Sending escalation to dispatcher %s (%s)", dispatcher.name, chat_id)
        send_whatsapp_reply(chat_id, message)


def generate_technician_assignment_message(
    technician_name: str,
    ticket_number: str,
    tenant_name: str,
    building_name: str,
    apartment: str,
    description: str,
    category: str,
    urgency: str,
    scheduled_time: str,
) -> str:
    """Generate a technician assignment notification via GPT. Falls back to hardcoded template."""
    from .classifier import _get_client, _load_prompt

    fallback = (
        f"🔧 *Новая заявка: {ticket_number}*\n\n"
        f"*Жилец:* {tenant_name}\n"
        f"*Адрес:* {building_name}, кв. {apartment}\n"
        f"*Проблема:* {description}\n"
        f"*Категория:* {category}\n"
        f"*Срочность:* {urgency}\n"
        f"*Время визита:* {scheduled_time}\n\n"
        "При возникновении вопросов свяжитесь с диспетчером."
    )

    try:
        client = _get_client()
        prompt = _load_prompt("technician_assignment")
        if not prompt:
            return fallback

        user_content = (
            f"TICKET DETAILS:\n"
            f"Ticket number: {ticket_number}\n"
            f"Technician name: {technician_name}\n"
            f"Tenant name: {tenant_name}\n"
            f"Building: {building_name}\n"
            f"Apartment: {apartment}\n"
            f"Problem: {description}\n"
            f"Category: {category}\n"
            f"Urgency: {urgency}\n"
            f"Scheduled time: {scheduled_time}"
        )

        response = client.chat.completions.create(
            model="gpt-5.4",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_completion_tokens=300,
        )
        return response.choices[0].message.content.strip()

    except Exception:
        logger.exception("Failed to generate technician assignment message via GPT, using fallback")
        return fallback


def notify_technician(db: Session, technician_id: str, message: str) -> None:
    """Send a WhatsApp message to the assigned technician."""
    tech = (
        db.query(models.User)
        .filter(models.User.id == technician_id)
        .first()
    )
    if not tech or not tech.phone:
        logger.warning("Cannot notify technician %s: not found or no phone", technician_id)
        return

    digits = _normalize_phone(tech.phone)
    if not digits:
        return
    chat_id = f"{digits}@c.us"
    logger.info("Sending assignment notification to technician %s (%s)", tech.name, chat_id)
    send_whatsapp_reply(chat_id, message)
