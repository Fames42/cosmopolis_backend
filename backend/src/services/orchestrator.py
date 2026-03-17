"""Conversation orchestrator — two-step pipeline: Route → Action → Write."""

import json
import re
import logging
from datetime import datetime, timezone, date as date_type, timedelta
from sqlalchemy.orm import Session

from ..models import (
    Tenant, Conversation, Message,
    ConversationStatusEnum, ConversationStateEnum, ScenarioEnum,
    MessageSenderEnum, MessageTypeEnum,
)
from ..schemas import AgentResponse, RouterResponse, CollectedFields, BackendNotes
from .classifier import step1_route, step2_write
from .notifier import (
    send_escalation_alert, generate_escalation_message, notify_dispatchers,
    generate_technician_assignment_message, notify_technician,
)
from .scheduler import find_available_slots, find_slots_for_date, create_ticket_from_context, verify_slot_available

logger = logging.getLogger("uvicorn.error")


# ── next_step → DB state mapping ─────────────────────────────────────────────

NEXT_STEP_STATE_MAP = {
    "greet": ConversationStateEnum.gathering,
    "clarify_intent": ConversationStateEnum.gathering,
    "ask_problem_details": ConversationStateEnum.service_collecting_details,
    "ask_danger": ConversationStateEnum.service_assessing_urgency,
    "ask_preferred_day": ConversationStateEnum.service_collecting_details,
    "offer_slots": ConversationStateEnum.service_scheduling,
    "confirm": ConversationStateEnum.service_ready_for_ticket,
    "answer_faq": ConversationStateEnum.classified_faq,
    "answer_billing": ConversationStateEnum.classified_billing,
    "log_announcement": ConversationStateEnum.classified_announcement,
    "close_or_continue": ConversationStateEnum.gathering,
    "escalate": ConversationStateEnum.escalated_to_human,
    "cancel": ConversationStateEnum.closed,
}

INTENT_SCENARIO_MAP = {
    "service": ScenarioEnum.service,
    "faq": ScenarioEnum.faq,
    "billing": ScenarioEnum.billing,
    "announcement": ScenarioEnum.announcement,
    "unknown": ScenarioEnum.unknown,
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _digits_only(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def _normalize_kz(digits: str) -> str:
    """Normalize KZ phone: replace leading 8 with 7 (87xx → 77xx)."""
    if len(digits) == 11 and digits.startswith("8"):
        return "7" + digits[1:]
    return digits


def _find_tenant(db: Session, phone: str) -> Tenant | None:
    digits = _normalize_kz(_digits_only(phone))
    if not digits:
        return None
    suffix = digits[-3:]
    candidates = db.query(Tenant).filter(Tenant.phone.contains(suffix)).all()
    for t in candidates:
        if _normalize_kz(_digits_only(t.phone)) == digits:
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
            conv.reopened_at = datetime.now(timezone.utc)
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


def _get_message_history(db: Session, conversation_id: int, since: datetime | None = None) -> list[dict[str, str]]:
    query = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
    )
    if since:
        query = query.filter(Message.created_at >= since)
    messages = (
        query.order_by(Message.created_at.asc())
        .limit(20)
        .all()
    )
    history = []
    for m in messages:
        role = "tenant" if m.sender == MessageSenderEnum.tenant else "ai"
        if m.content:
            history.append({"role": role, "content": m.content})
    return history


def _save_message(
    db: Session,
    conversation_id: int,
    sender: MessageSenderEnum,
    content: str,
    image_base64: str | None = None,
) -> Message:
    if image_base64:
        msg_type = MessageTypeEnum.image if not content or content == "[Фото]" else MessageTypeEnum.mixed
    else:
        msg_type = MessageTypeEnum.text
    msg = Message(
        conversation_id=conversation_id,
        sender=sender,
        message_type=msg_type,
        content=content,
        media_url=image_base64,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


ESCALATION_COOLDOWN_HOURS = 12


def _escalate(
    db: Session,
    conv: Conversation,
    tenant: Tenant,
    phone: str,
    content: str,
    scenario: ScenarioEnum,
    confidence: float | None,
    history: list[dict[str, str]],
) -> None:
    """Transition conversation to escalated_to_human, notify group + dispatchers."""
    conv.state = ConversationStateEnum.escalated_to_human
    conv.scenario = scenario
    conv.classifier_confidence = confidence
    conv.escalated_at = datetime.now(timezone.utc)

    building_name = tenant.building.name if tenant.building else "—"
    apartment = tenant.apartment or "—"

    # Group chat alert (existing)
    send_escalation_alert(
        tenant_name=tenant.name,
        tenant_phone=phone,
        building_name=building_name,
        apartment=apartment,
        last_message=content,
    )

    # Personal dispatcher notifications via GPT-generated message
    message = generate_escalation_message(
        tenant_name=tenant.name,
        tenant_phone=phone,
        building_name=building_name,
        apartment=apartment,
        history=history,
    )
    notify_dispatchers(db, message)


def _update_context(conv: Conversation, updates: dict) -> dict:
    """Merge updates into conversation context_data (handles SQLAlchemy JSON mutation)."""
    ctx = dict(conv.context_data or {})
    ctx.update(updates)
    conv.context_data = ctx
    return ctx


# ── State context builder for Router ─────────────────────────────────────────

def _build_state_context(conv: Conversation) -> str:
    """Serialize conversation state + context_data into a text block for the Router."""
    from .scheduler import TZ_ALMATY
    now_almaty = datetime.now(TZ_ALMATY)
    ctx = conv.context_data or {}
    lines = [
        f"TODAY: {now_almaty.strftime('%Y-%m-%d')} ({now_almaty.strftime('%A')})",
        f"CURRENT_TIME: {now_almaty.strftime('%H:%M')}",
        f"CURRENT_STATE: {conv.state.value}",
        "",
        "ALREADY_COLLECTED:",
        f"  service_category: {ctx.get('service_category') or ctx.get('category') or 'null'}",
        f"  urgency: {ctx.get('urgency') or 'null'}",
        f"  problem: {ctx.get('description') or ctx.get('problem') or 'null'}",
        f"  location: {ctx.get('location') or 'null'}",
        f"  danger_now: {ctx.get('danger_now') or 'null'}",
        f"  preferred_date: {ctx.get('preferred_date') or 'null'}",
        f"  photo_received: {ctx.get('photo_received') or 'null'}",
    ]

    # Include offered slots if present
    offered = ctx.get("offered_slots")
    if offered:
        lines.append("")
        lines.append("OFFERED_SLOTS:")
        for i, s in enumerate(offered):
            lines.append(f"  {i}. {s['start']} — {s['end']} (Technician: {s['technician_name']})")

    # Include selected slot if present
    selected_idx = ctx.get("selected_slot_index")
    if selected_idx is not None and offered and selected_idx < len(offered):
        slot = offered[selected_idx]
        lines.append("")
        lines.append(f"SELECTED_SLOT: index={selected_idx}, {slot['start']} — {slot['end']} (Technician: {slot['technician_name']})")

    # Include ticket number if already created
    ticket_number = ctx.get("ticket_number")
    if ticket_number:
        lines.append("")
        lines.append(f"TICKET_CREATED: {ticket_number}")

    lines.append("")
    lines.append("Analyze the tenant's latest message in the context above and return the routing JSON.")

    return "\n".join(lines)


# ── Router output → legacy AgentResponse mapping ────────────────────────────

def _router_to_agent_response(router: RouterResponse, reply: str) -> AgentResponse:
    """Map Router output to the legacy AgentResponse for backward compatibility."""
    classified = router.intent is not None and router.intent != "unknown"
    return AgentResponse(
        reply=reply,
        classified=classified,
        scenario=router.intent,
        confidence=0.9 if classified else None,
        subtype=router.service_category,
        requires_human=router.requires_human,
    )


# ── Main entry point ─────────────────────────────────────────────────────────

def save_incoming_message(
    db: Session, phone: str, content: str, *, image_base64: str | None = None,
) -> tuple[Tenant | None, Conversation | None, str]:
    """Save an incoming tenant message to DB without processing it."""
    tenant = _find_tenant(db, phone)
    if not tenant:
        return None, None, ""

    chat_id = f"{_digits_only(phone)}@c.us"
    conv = _get_or_create_conversation(db, tenant, chat_id)
    _save_message(db, conv.id, MessageSenderEnum.tenant, content, image_base64=image_base64)

    return tenant, conv, chat_id


def process_conversation(
    db: Session, conv: Conversation, tenant: Tenant, phone: str,
) -> tuple[str, str, AgentResponse | None]:
    """Two-step pipeline: Route → Action → Write.

    1. Build state context from conversation
    2. Call Router (step 1) for classification + workflow control
    3. Execute backend actions (find slots, create ticket, escalate)
    4. Call Writer (step 2) for tenant-facing reply
    5. Update state and save reply

    Returns:
        Tuple of (reply_text, conversation_state, agent_response_or_none).
    """
    # ── Check if agent support is enabled for tenant ────────────────────
    if not tenant.agent_enabled:
        logger.info("Agent support disabled for tenant %s, skipping AI processing", tenant.id)
        return (
            "Автоматическая поддержка отключена для вашего аккаунта. "
            "Пожалуйста, свяжитесь с управляющей компанией напрямую.",
            "agent_disabled",
            None,
        )

    # ── Check escalation state ──────────────────────────────────────────
    if conv.state == ConversationStateEnum.escalated_to_human:
        if conv.escalated_at:
            escalated_at = conv.escalated_at.replace(tzinfo=timezone.utc) if conv.escalated_at.tzinfo is None else conv.escalated_at
            elapsed = datetime.now(timezone.utc) - escalated_at
            if elapsed < timedelta(hours=ESCALATION_COOLDOWN_HOURS):
                # Still escalated — save message but don't respond
                logger.info("Conversation %s is escalated, AI paused (%.1fh remaining)",
                            conv.id, ESCALATION_COOLDOWN_HOURS - elapsed.total_seconds() / 3600)
                return "", conv.state.value, None

        # Cooldown expired — auto-reset conversation
        logger.info("Escalation cooldown expired for conversation %s, resetting", conv.id)
        conv.state = ConversationStateEnum.new_conversation
        conv.escalated_at = None
        conv.context_data = {}
        conv.scenario = None
        conv.reopened_at = datetime.now(timezone.utc)
        db.commit()

    history = _get_message_history(db, conv.id, since=conv.reopened_at)

    # Extract last tenant message
    last_tenant_content = ""
    for msg in reversed(history):
        if msg["role"] == "tenant":
            last_tenant_content = msg["content"]
            break

    # ── Step 1: Route ────────────────────────────────────────────────────
    state_context = _build_state_context(conv)
    router_raw = step1_route(history, state_context)

    # Parse with Pydantic (graceful fallback on bad data)
    try:
        router = RouterResponse(**router_raw)
    except Exception:
        logger.warning("Failed to parse router response, using defaults: %s", router_raw)
        router = RouterResponse(
            intent="unknown", requires_human=True, next_step="escalate",
        )

    logger.info(
        "Router: intent=%s next_step=%s cancel=%s human=%s ticket=%s",
        router.intent, router.next_step, router.cancel_requested,
        router.requires_human, router.ready_for_ticket,
    )

    # ── Step 2: Execute backend actions ──────────────────────────────────
    backend_results: dict = {}

    # Handle cancellation
    if router.cancel_requested:
        conv.state = ConversationStateEnum.closed
        conv.status = ConversationStatusEnum.closed
        reply = step2_write(router_raw, last_tenant_content, {"action": "cancelled"})
        db.commit()
        _save_message(db, conv.id, MessageSenderEnum.ai, reply)
        agent_resp = _router_to_agent_response(router, reply)
        return reply, conv.state.value, agent_resp

    # Handle escalation
    if router.requires_human:
        scenario = INTENT_SCENARIO_MAP.get(router.intent or "unknown", ScenarioEnum.unknown)
        _escalate(db, conv, tenant, phone, last_tenant_content, scenario, None, history)
        reply = step2_write(router_raw, last_tenant_content, {"action": "escalated"})
        db.commit()
        _save_message(db, conv.id, MessageSenderEnum.ai, reply)
        agent_resp = _router_to_agent_response(router, reply)
        return reply, conv.state.value, agent_resp

    # Update collected fields into context_data
    cf = router.collected_fields
    field_updates = {}
    if cf.problem:
        field_updates["description"] = cf.problem
        field_updates["problem"] = cf.problem
    if cf.location:
        field_updates["location"] = cf.location
    if cf.danger_now is not None:
        field_updates["danger_now"] = cf.danger_now
    if cf.preferred_date:
        field_updates["preferred_date"] = cf.preferred_date
    if cf.photo_received is not None:
        field_updates["photo_received"] = cf.photo_received
    if router.service_category:
        field_updates["category"] = router.service_category
        field_updates["service_category"] = router.service_category
    if router.urgency:
        field_updates["urgency"] = router.urgency
    if field_updates:
        _update_context(conv, field_updates)

    # Update scenario if intent is known
    if router.intent and router.intent != "unknown":
        scenario = INTENT_SCENARIO_MAP.get(router.intent)
        if scenario:
            conv.scenario = scenario

    # Handle slot offering
    if router.next_step == "offer_slots":
        ctx = conv.context_data or {}
        category = ctx.get("category", "other")
        urgency = ctx.get("urgency", "medium")
        preferred_date_str = ctx.get("preferred_date")

        slots = []
        if preferred_date_str:
            # Tenant specified a preferred date — search that date only
            try:
                target = date_type.fromisoformat(preferred_date_str)
                slots = find_slots_for_date(db, category, target)
                logger.info("Slots for %s (%s): %d found", preferred_date_str, category, len(slots))
            except ValueError:
                logger.warning("Invalid preferred_date: %s", preferred_date_str)

            if not slots:
                # No slots on requested date — ask for another day (no fallback to other dates)
                _update_context(conv, {"preferred_date": None})
                backend_results["action"] = "no_slots_on_date"
                backend_results["requested_date"] = preferred_date_str
                router = router.model_copy(update={"next_step": "ask_preferred_day"})
                router_raw = router.model_dump()
        else:
            # No preferred date — search across urgency window
            slots = find_available_slots(db, category, urgency, num_slots=3)
            if not slots:
                slots = find_available_slots(db, category, "low", num_slots=3)

        if slots:
            _update_context(conv, {"offered_slots": slots, "slots_presented": True})
            backend_results["available_slots"] = [
                {"index": i, "start": s["start"], "end": s["end"], "technician_name": s["technician_name"]}
                for i, s in enumerate(slots)
            ]
        elif "action" not in backend_results:
            if urgency == "emergency":
                scenario = INTENT_SCENARIO_MAP.get(router.intent or "unknown", ScenarioEnum.unknown)
                _escalate(db, conv, tenant, phone, last_tenant_content, scenario, None, history)
                backend_results["action"] = "no_slots_emergency_escalated"
            else:
                backend_results["action"] = "no_slots_available"

    # Handle slot selection from router
    if cf.time_slot is not None:
        ctx = conv.context_data or {}
        offered = ctx.get("offered_slots", [])
        selected_idx = cf.time_slot
        logger.info("Slot selection: index=%s, offered=%d slots", selected_idx, len(offered))

        if 0 <= selected_idx < len(offered):
            slot = offered[selected_idx]
            logger.info("Selected slot: %s — %s (tech: %s)", slot["start"], slot["end"], slot["technician_name"])
            if verify_slot_available(db, slot["technician_id"], slot["start"]):
                _update_context(conv, {"selected_slot_index": selected_idx})
                backend_results["selected_slot"] = {
                    "index": selected_idx,
                    "start": slot["start"],
                    "end": slot["end"],
                    "technician_name": slot["technician_name"],
                }
            else:
                # Slot taken — re-fetch
                _update_context(conv, {"slots_presented": False})
                category = ctx.get("category", "other")
                urgency = ctx.get("urgency", "medium")
                new_slots = find_available_slots(db, category, urgency, num_slots=3)
                if new_slots:
                    _update_context(conv, {"offered_slots": new_slots, "slots_presented": True})
                    backend_results["available_slots"] = [
                        {"index": i, "start": s["start"], "end": s["end"], "technician_name": s["technician_name"]}
                        for i, s in enumerate(new_slots)
                    ]
                    backend_results["slot_unavailable"] = True
                    # Override next_step since we need to re-offer
                    router = router.model_copy(update={"next_step": "offer_slots", "ready_for_confirmation": False})
                    router_raw = router.model_dump()

    # Build confirmation summary if needed
    if router.ready_for_confirmation or router.next_step == "confirm":
        ctx = conv.context_data or {}
        offered = ctx.get("offered_slots", [])
        selected_idx = ctx.get("selected_slot_index")
        slot = offered[selected_idx] if selected_idx is not None and selected_idx < len(offered) else {}
        backend_results["confirmation_summary"] = {
            "problem": ctx.get("description") or ctx.get("problem", "N/A"),
            "category": ctx.get("category", "N/A"),
            "urgency": ctx.get("urgency", "N/A"),
            "scheduled_start": slot.get("start", "N/A"),
            "scheduled_end": slot.get("end", "N/A"),
            "technician_name": slot.get("technician_name", "N/A"),
        }

    # Handle ticket creation (guard against duplicates)
    ctx = conv.context_data or {}
    if router.ready_for_ticket and not ctx.get("ticket_number"):
        ticket = create_ticket_from_context(db, tenant.id, ctx, conv.id)
        _update_context(conv, {"ticket_number": ticket.ticket_number})
        backend_results["ticket_created"] = {
            "ticket_number": ticket.ticket_number,
        }

        # Notify the assigned technician via WhatsApp
        if ticket.assigned_to:
            building_name = tenant.building.name if tenant.building else "—"
            apartment = tenant.apartment or "—"
            offered = ctx.get("offered_slots", [])
            selected_idx = ctx.get("selected_slot_index", 0)
            slot = offered[selected_idx] if selected_idx < len(offered) else {}
            tech_msg = generate_technician_assignment_message(
                technician_name=slot.get("technician_name", "—"),
                ticket_number=ticket.ticket_number,
                tenant_name=tenant.name,
                building_name=building_name,
                apartment=apartment,
                description=ticket.description or "",
                category=ticket.category or "",
                urgency=ticket.urgency or "",
                scheduled_time=slot.get("start", ""),
            )
            notify_technician(db, ticket.assigned_to, tech_msg)

    # Handle already-created ticket (tenant messages after ticket_created)
    ctx = conv.context_data or {}
    if ctx.get("ticket_number") and "ticket_created" not in backend_results:
        backend_results["existing_ticket"] = ctx["ticket_number"]

    # ── Step 3: Write ────────────────────────────────────────────────────
    reply = step2_write(router_raw, last_tenant_content, backend_results or None)

    # ── Step 4: Update state ─────────────────────────────────────────────
    new_state = NEXT_STEP_STATE_MAP.get(router.next_step)
    if new_state:
        conv.state = new_state
        if new_state == ConversationStateEnum.closed:
            conv.status = ConversationStatusEnum.closed

    # If ticket was just created, move to ticket_created
    if "ticket_created" in backend_results:
        conv.state = ConversationStateEnum.ticket_created

    # After close_or_continue, clear context and history so next message starts fresh
    if router.next_step == "close_or_continue":
        conv.context_data = {}
        conv.scenario = None
        conv.reopened_at = datetime.now(timezone.utc)

    db.commit()

    _save_message(db, conv.id, MessageSenderEnum.ai, reply)

    agent_resp = _router_to_agent_response(router, reply)
    return reply, conv.state.value, agent_resp


def handle_message(
    db: Session, phone: str, content: str, *, image_base64: str | None = None,
) -> tuple[str, str, AgentResponse | None]:
    """Process an incoming message and return (reply, state, agent_response).

    Convenience wrapper that saves the message and immediately processes.
    For buffered processing, use save_incoming_message + process_conversation separately.
    """
    tenant, conv, chat_id = save_incoming_message(db, phone, content, image_base64=image_base64)
    if not tenant:
        return (
            "Извините, ваш номер не найден в системе. Обратитесь в управляющую компанию для регистрации.",
            "unknown_tenant",
            None,
        )

    return process_conversation(db, conv, tenant, phone)
