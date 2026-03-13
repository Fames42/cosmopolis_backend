"""Conversation state machine — routes incoming WhatsApp messages through states."""

import json
import re
import logging
from sqlalchemy.orm import Session

from ..models import (
    Tenant, Conversation, Message,
    ConversationStatusEnum, ConversationStateEnum, ScenarioEnum,
    MessageSenderEnum, MessageTypeEnum,
)
from ..schemas import AgentResponse
from .classifier import process_message, call_llm
from .notifier import send_escalation_alert
from .scheduler import find_available_slots, create_ticket_from_context, verify_slot_available

logger = logging.getLogger("uvicorn.error")

CONFIDENCE_THRESHOLD = 0.65

SCENARIO_STATE_MAP = {
    "service": ConversationStateEnum.classified_service,
    "faq": ConversationStateEnum.classified_faq,
    "billing": ConversationStateEnum.classified_billing,
    "announcement": ConversationStateEnum.classified_announcement,
}


# ── LLM instruction prompts for each service-flow state ──────────────────────

SERVICE_DETAILS_INSTRUCTION = """You are collecting details about a maintenance/service request.

Based on the conversation so far, extract what you know AND reply to the tenant.

Return ONLY valid JSON:
{
  "reply": "your reply to the tenant (in their language)",
  "category": "plumbing" | "electrical" | "heating" | "appliance" | "structural" | "ventilation" | "sewage" | "internet" | "other" | null,
  "urgency": "emergency" | "high" | "medium" | "low" | null,
  "description": "concise summary of the problem" or null,
  "location": "where in the apartment" or null,
  "details_complete": true or false,
  "cancel_requested": false
}

Rules:
- If category, urgency, and description are all clear → set details_complete=true
- If something is missing, ask for it in your reply (1 question at a time)
- Match the tenant's language
- If tenant says "cancel", "never mind", "отмена" → set cancel_requested=true
- Be concise: 1-2 sentences"""

URGENCY_CONFIRM_INSTRUCTION = """The tenant reported an emergency/high-urgency issue.

Confirm the urgency level with the tenant. Ask if the situation is actively dangerous right now.

Return ONLY valid JSON:
{
  "reply": "your reply to the tenant (in their language)",
  "urgency_confirmed": true or false,
  "revised_urgency": "emergency" | "high" | "medium" | "low" | null,
  "cancel_requested": false
}

Rules:
- If tenant confirms danger → urgency_confirmed=true
- If tenant says it's not urgent right now → revised_urgency with lower level
- Match the tenant's language
- If tenant says "cancel" → cancel_requested=true"""

SLOT_PRESENT_INSTRUCTION = """Present the available time slots to the tenant as numbered options.

Return ONLY valid JSON:
{
  "reply": "your reply presenting the numbered options (in their language)",
  "cancel_requested": false
}

Rules:
- Present each slot as a numbered option (1, 2, 3...)
- Include the date, time, and technician name
- Ask which option works best
- Match the tenant's language
- Be friendly and concise
- If tenant says "cancel" → cancel_requested=true"""

SLOT_SELECT_INSTRUCTION = """The tenant is choosing from the time slots that were offered.

Based on the tenant's reply, determine which slot they selected.

Return ONLY valid JSON:
{
  "reply": "your reply acknowledging their choice (in their language)",
  "selected_index": 0-based index of the selected slot or null,
  "needs_more_options": true or false,
  "cancel_requested": false
}

Rules:
- If tenant clearly picks a number (e.g., "1", "first", "первый") → set selected_index (0-based)
- If tenant says "other options" or "none of these" → needs_more_options=true
- If unclear, ask them to clarify in the reply
- Match the tenant's language
- If tenant says "cancel" → cancel_requested=true"""

CONFIRM_INSTRUCTION = """Summarize ALL collected details and ask the tenant for final confirmation before creating a ticket.

Return ONLY valid JSON:
{
  "reply": "your summary and confirmation request (in their language)",
  "confirmed": true or false,
  "cancel_requested": false
}

Rules:
- If this is the first time showing the summary (the tenant hasn't responded yet), set confirmed=false and present the summary asking them to confirm
- If the tenant already confirmed (said "yes", "да", "correct", etc.), set confirmed=true
- If tenant says "no" or wants changes, set confirmed=false and ask what to change
- Include: problem description, category, urgency, scheduled time, technician name
- Match the tenant's language
- If tenant says "cancel" → cancel_requested=true"""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _digits_only(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def _find_tenant(db: Session, phone: str) -> Tenant | None:
    digits = _digits_only(phone)
    if not digits:
        return None
    suffix = digits[-3:]
    candidates = db.query(Tenant).filter(Tenant.phone.contains(suffix)).all()
    for t in candidates:
        if _digits_only(t.phone) == digits:
            return t
    return None


def _get_or_create_conversation(db: Session, tenant: Tenant, chat_id: str) -> Conversation:
    conv = (
        db.query(Conversation)
        .filter(Conversation.whatsapp_chat_id == chat_id)
        .first()
    )
    if conv:
        if conv.status == ConversationStatusEnum.closed:
            conv.status = ConversationStatusEnum.open
            conv.state = ConversationStateEnum.new_conversation
            conv.scenario = None
            conv.classifier_confidence = None
            conv.context_data = {}
            db.commit()
            db.refresh(conv)
        return conv

    conv = Conversation(
        tenant_id=tenant.id,
        whatsapp_chat_id=chat_id,
        status=ConversationStatusEnum.open,
        state=ConversationStateEnum.new_conversation,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def _get_message_history(db: Session, conversation_id: int) -> list[dict[str, str]]:
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(20)
        .all()
    )
    history = []
    for m in messages:
        role = "tenant" if m.sender == MessageSenderEnum.tenant else "ai"
        if m.content:
            history.append({"role": role, "content": m.content})
    return history


def _save_message(db: Session, conversation_id: int, sender: MessageSenderEnum, content: str) -> Message:
    msg = Message(
        conversation_id=conversation_id,
        sender=sender,
        message_type=MessageTypeEnum.text,
        content=content,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def _escalate(
    conv: Conversation,
    tenant: Tenant,
    phone: str,
    content: str,
    scenario: ScenarioEnum,
    confidence: float | None,
) -> None:
    """Transition conversation to escalated_to_human and send alert."""
    conv.state = ConversationStateEnum.escalated_to_human
    conv.scenario = scenario
    conv.classifier_confidence = confidence

    send_escalation_alert(
        tenant_name=tenant.name,
        tenant_phone=phone,
        building_name=tenant.building.name if tenant.building else "—",
        apartment=tenant.apartment or "—",
        last_message=content,
    )


def _update_context(conv: Conversation, updates: dict) -> dict:
    """Merge updates into conversation context_data (handles SQLAlchemy JSON mutation)."""
    ctx = dict(conv.context_data or {})
    ctx.update(updates)
    conv.context_data = ctx
    return ctx


def _cancel_reply(conv: Conversation) -> str:
    """Handle tenant cancellation at any service state."""
    conv.state = ConversationStateEnum.closed
    conv.status = ConversationStatusEnum.closed
    return "Хорошо, запрос отменён. Если понадобится помощь — напишите в любое время."


# ── Service flow state handlers ──────────────────────────────────────────────

def _handle_classified_service(
    db: Session, conv: Conversation, tenant: Tenant, content: str, history: list[dict],
) -> str:
    """Entry point after classification. Initialize context and immediately collect details."""
    _update_context(conv, {
        "subtype": (conv.context_data or {}).get("subtype"),
    })
    conv.state = ConversationStateEnum.service_collecting_details
    # Fall through to collecting details immediately
    return _handle_collecting_details(db, conv, tenant, content, history)


def _handle_collecting_details(
    db: Session, conv: Conversation, tenant: Tenant, content: str, history: list[dict],
) -> str:
    """LLM extracts category, urgency, description. Asks for missing info."""
    ctx = conv.context_data or {}
    extra = ""
    if ctx.get("category") or ctx.get("urgency"):
        extra = f"Previously extracted — category: {ctx.get('category')}, urgency: {ctx.get('urgency')}, description: {ctx.get('description')}"

    data = call_llm(history, SERVICE_DETAILS_INSTRUCTION, extra)

    if data.get("error"):
        return "Извините, произошла техническая ошибка. Пожалуйста, повторите ваш запрос."

    if data.get("cancel_requested"):
        return _cancel_reply(conv)

    # Store extracted details
    updates = {}
    for key in ("category", "urgency", "description", "location"):
        if data.get(key):
            updates[key] = data[key]
    _update_context(conv, updates)

    if data.get("details_complete"):
        urgency = (conv.context_data or {}).get("urgency", "medium").lower()
        if urgency in ("emergency", "high"):
            conv.state = ConversationStateEnum.service_assessing_urgency
        else:
            conv.state = ConversationStateEnum.service_scheduling
    # else: stay in service_collecting_details

    return data.get("reply", "")


def _handle_assessing_urgency(
    db: Session, conv: Conversation, tenant: Tenant, content: str, history: list[dict],
) -> str:
    """Confirm emergency/high urgency with tenant."""
    ctx = conv.context_data or {}
    extra = f"Current urgency level: {ctx.get('urgency', 'unknown')}. Problem: {ctx.get('description', 'unknown')}"

    data = call_llm(history, URGENCY_CONFIRM_INSTRUCTION, extra)

    if data.get("error"):
        # Skip urgency confirmation on error, proceed to scheduling
        conv.state = ConversationStateEnum.service_scheduling
        return "Давайте подберём удобное время для визита специалиста."

    if data.get("cancel_requested"):
        return _cancel_reply(conv)

    if data.get("urgency_confirmed"):
        _update_context(conv, {"urgency_confirmed": True})
        conv.state = ConversationStateEnum.service_scheduling
    elif data.get("revised_urgency"):
        _update_context(conv, {"urgency": data["revised_urgency"], "urgency_confirmed": True})
        conv.state = ConversationStateEnum.service_scheduling
    # else: stay, LLM asked a follow-up

    return data.get("reply", "")


def _handle_scheduling(
    db: Session, conv: Conversation, tenant: Tenant, content: str, history: list[dict],
) -> str:
    """Find slots and present them, or process tenant's slot selection."""
    ctx = conv.context_data or {}

    if not ctx.get("slots_presented"):
        # Phase 1: Find and present slots
        category = ctx.get("category", "other")
        urgency = ctx.get("urgency", "medium")
        slots = find_available_slots(db, category, urgency, num_slots=3)

        # If no slots found with urgency window, widen to maximum window
        if not slots:
            slots = find_available_slots(db, category, "low", num_slots=3)

        if not slots:
            if urgency.lower() == "emergency":
                # Only escalate for emergency when no technicians available at all
                _escalate(conv, tenant, "", content, ScenarioEnum.service, None)
                return "К сожалению, нет доступных специалистов для экстренного вызова. Ваш запрос передан диспетчеру."
            else:
                # Non-emergency: inform tenant, no escalation
                return "К сожалению, сейчас нет доступных временных слотов. Попробуйте обратиться позже или уточните другую категорию проблемы."

        _update_context(conv, {"offered_slots": slots, "slots_presented": True})

        # Format slots for LLM
        slots_text = "AVAILABLE TIME SLOTS:\n"
        for i, s in enumerate(slots):
            slots_text += f"{i+1}. {s['start']} — {s['end']} (Technician: {s['technician_name']})\n"

        data = call_llm(history, SLOT_PRESENT_INSTRUCTION, slots_text)

        if data.get("cancel_requested"):
            return _cancel_reply(conv)

        return data.get("reply", "")

    else:
        # Phase 2: Process tenant's selection
        offered = ctx.get("offered_slots", [])
        slots_text = "PREVIOUSLY OFFERED SLOTS:\n"
        for i, s in enumerate(offered):
            slots_text += f"{i+1}. {s['start']} — {s['end']} (Technician: {s['technician_name']})\n"

        data = call_llm(history, SLOT_SELECT_INSTRUCTION, slots_text)

        if data.get("error"):
            # Re-present the same slots instead of escalating
            return "Извините, не удалось обработать ваш выбор. Пожалуйста, укажите номер слота из предложенных вариантов."

        if data.get("cancel_requested"):
            return _cancel_reply(conv)

        if data.get("needs_more_options"):
            # Widen search
            slots = find_available_slots(db, ctx.get("category", "other"), "low", num_slots=5)
            if not slots:
                return "К сожалению, других доступных слотов нет. Пожалуйста, выберите из предложенных ранее вариантов."
            _update_context(conv, {"offered_slots": slots, "slots_presented": False})
            return _handle_scheduling(db, conv, tenant, content, history)

        selected = data.get("selected_index")
        if selected is not None and 0 <= selected < len(offered):
            slot = offered[selected]
            if not verify_slot_available(db, slot["technician_id"], slot["start"]):
                _update_context(conv, {"slots_presented": False})
                return _handle_scheduling(db, conv, tenant, content, history)

            _update_context(conv, {"selected_slot_index": selected})
            conv.state = ConversationStateEnum.service_ready_for_ticket
            return data.get("reply", "")
        else:
            return data.get("reply", "")


def _handle_ready_for_ticket(
    db: Session, conv: Conversation, tenant: Tenant, content: str, history: list[dict],
) -> str:
    """Summarize details and ask for final confirmation."""
    ctx = conv.context_data or {}
    offered = ctx.get("offered_slots", [])
    selected_idx = ctx.get("selected_slot_index", 0)
    slot = offered[selected_idx] if selected_idx < len(offered) else {}

    summary = (
        f"BOOKING SUMMARY:\n"
        f"Problem: {ctx.get('description', 'N/A')}\n"
        f"Category: {ctx.get('category', 'N/A')}\n"
        f"Urgency: {ctx.get('urgency', 'N/A')}\n"
        f"Scheduled: {slot.get('start', 'N/A')} — {slot.get('end', 'N/A')}\n"
        f"Technician: {slot.get('technician_name', 'N/A')}"
    )

    data = call_llm(history, CONFIRM_INSTRUCTION, summary)

    if data.get("error"):
        return "Извините, произошла ошибка. Пожалуйста, подтвердите бронирование: ответьте «да» или «нет»."

    if data.get("cancel_requested"):
        return _cancel_reply(conv)

    if data.get("confirmed"):
        ticket = create_ticket_from_context(db, tenant.id, ctx)
        _update_context(conv, {"ticket_number": ticket.ticket_number})
        conv.state = ConversationStateEnum.ticket_created
        return data.get("reply", "")

    return data.get("reply", "")


def _handle_ticket_created(
    db: Session, conv: Conversation, tenant: Tenant, content: str, history: list[dict],
) -> str:
    """Ticket already created — acknowledge and close."""
    ctx = conv.context_data or {}
    ticket_number = ctx.get("ticket_number", "")
    conv.state = ConversationStateEnum.closed
    conv.status = ConversationStatusEnum.closed
    return f"Ваша заявка {ticket_number} уже создана. Если возникнут вопросы — напишите нам."


# ── State handler dispatch table ─────────────────────────────────────────────

SERVICE_STATE_HANDLERS = {
    ConversationStateEnum.classified_service: _handle_classified_service,
    ConversationStateEnum.service_collecting_details: _handle_collecting_details,
    ConversationStateEnum.service_assessing_urgency: _handle_assessing_urgency,
    ConversationStateEnum.service_scheduling: _handle_scheduling,
    ConversationStateEnum.service_ready_for_ticket: _handle_ready_for_ticket,
    ConversationStateEnum.ticket_created: _handle_ticket_created,
}


# ── Main entry point ─────────────────────────────────────────────────────────

def handle_message(db: Session, phone: str, content: str) -> tuple[str, str, AgentResponse | None]:
    """Process an incoming message and return (reply, state, agent_response).

    Args:
        db: Database session.
        phone: Tenant phone (digits or formatted).
        content: Message text.

    Returns:
        Tuple of (reply_text, conversation_state, agent_response_or_none).
    """
    tenant = _find_tenant(db, phone)
    if not tenant:
        return (
            "Извините, ваш номер не найден в системе. Обратитесь в управляющую компанию для регистрации.",
            "unknown_tenant",
            None,
        )

    chat_id = f"{_digits_only(phone)}@c.us"
    conv = _get_or_create_conversation(db, tenant, chat_id)

    _save_message(db, conv.id, MessageSenderEnum.tenant, content)

    history = _get_message_history(db, conv.id)
    agent_resp = None
    reply = ""

    # ── Classification phase ─────────────────────────────────────────────
    if conv.state in (
        ConversationStateEnum.new_conversation,
        ConversationStateEnum.gathering,
    ):
        agent_resp = process_message(history)
        reply = agent_resp.reply

        if agent_resp.requires_human:
            _escalate(conv, tenant, phone, content, ScenarioEnum.unknown, agent_resp.confidence)
        elif agent_resp.classified and agent_resp.confidence is not None and agent_resp.confidence >= CONFIDENCE_THRESHOLD:
            scenario_key = agent_resp.scenario
            if scenario_key in SCENARIO_STATE_MAP:
                conv.state = SCENARIO_STATE_MAP[scenario_key]
                conv.scenario = ScenarioEnum(scenario_key)
                conv.classifier_confidence = agent_resp.confidence

                # For service: store subtype in context and immediately start flow
                if scenario_key == "service":
                    _update_context(conv, {"subtype": agent_resp.subtype})
            else:
                _escalate(conv, tenant, phone, content, ScenarioEnum.unknown, agent_resp.confidence)
        elif agent_resp.classified and agent_resp.confidence is not None and agent_resp.confidence < CONFIDENCE_THRESHOLD:
            scenario = ScenarioEnum(agent_resp.scenario) if agent_resp.scenario in [e.value for e in ScenarioEnum] else ScenarioEnum.unknown
            _escalate(conv, tenant, phone, content, scenario, agent_resp.confidence)
        else:
            conv.state = ConversationStateEnum.gathering

    # ── Service flow states ──────────────────────────────────────────────
    elif conv.state in SERVICE_STATE_HANDLERS:
        handler = SERVICE_STATE_HANDLERS[conv.state]
        reply = handler(db, conv, tenant, content, history)

    # ── Escalated ────────────────────────────────────────────────────────
    elif conv.state == ConversationStateEnum.escalated_to_human:
        reply = "Ваш запрос уже передан диспетчеру. Ожидайте ответа."

    # ── Other classified scenarios (faq, billing, announcement) ──────────
    elif conv.state in (
        ConversationStateEnum.classified_faq,
        ConversationStateEnum.classified_billing,
        ConversationStateEnum.classified_announcement,
    ):
        reply = f"Ваш запрос уже классифицирован как '{conv.scenario.value if conv.scenario else 'unknown'}'. Обработка будет добавлена в следующей версии."

    # ── Closed ───────────────────────────────────────────────────────────
    elif conv.state == ConversationStateEnum.closed:
        reply = "Ваш предыдущий запрос был завершён. Если нужна помощь — начните новый диалог."

    else:
        reply = "Ваш запрос обрабатывается. Ожидайте."

    db.commit()

    _save_message(db, conv.id, MessageSenderEnum.ai, reply)

    return reply, conv.state.value, agent_resp
