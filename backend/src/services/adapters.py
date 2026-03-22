"""Backend implementations of agent protocols — bridges ORM to agent DTOs."""

import os
import re
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from .. import models
from ..models import (
    Tenant, Conversation, Message,
    ConversationStatusEnum, ConversationStateEnum, ScenarioEnum,
    MessageSenderEnum, MessageTypeEnum,
)
from ..agent.types import (
    TenantInfo,
    HistoryMessage,
    SlotInfo,
    TicketResult,
    TicketSummary,
    ConversationSnapshot,
    ConversationStateUpdate,
    ConversationState,
)
from ..agent.llm import OpenAILLMClient
from ..agent.engine import AgentEngine
from . import scheduler as scheduler_mod
from . import notifier as notifier_mod

logger = logging.getLogger("uvicorn.error")


# ── Phone helpers ────────────────────────────────────────────────────────────

def _digits_only(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def _normalize_kz(digits: str) -> str:
    if len(digits) == 11 and digits.startswith("8"):
        return "7" + digits[1:]
    return digits


# ── Enum converters ──────────────────────────────────────────────────────────

_STATE_TO_DB = {s.value: ConversationStateEnum(s.value) for s in ConversationState}
_DB_TO_STATE = {v: ConversationState(v.value) for v in ConversationStateEnum}

_SCENARIO_TO_DB = {
    "service": ScenarioEnum.service,
    "faq": ScenarioEnum.faq,
    "billing": ScenarioEnum.billing,
    "announcement": ScenarioEnum.announcement,
    "unknown": ScenarioEnum.unknown,
}


def _tenant_to_info(t: Tenant) -> TenantInfo:
    return TenantInfo(
        id=t.id,
        name=t.name,
        phone=t.phone,
        building_name=t.building.name if t.building else "—",
        apartment=t.apartment or "—",
        agent_enabled=t.agent_enabled,
    )


def _conv_to_snapshot(conv: Conversation) -> ConversationSnapshot:
    return ConversationSnapshot(
        id=conv.id,
        tenant_id=conv.tenant_id,
        chat_id=conv.whatsapp_chat_id,
        status=conv.status.value,
        state=_DB_TO_STATE[conv.state],
        scenario=conv.scenario.value if conv.scenario else None,
        context_data=dict(conv.context_data or {}),
        escalated_at=conv.escalated_at,
        reopened_at=conv.reopened_at,
    )


# ── SqlConversationStore ─────────────────────────────────────────────────────

class SqlConversationStore:
    def __init__(self, db: Session):
        self.db = db

    def find_tenant_by_phone(self, phone: str) -> TenantInfo | None:
        digits = _normalize_kz(_digits_only(phone))
        if not digits:
            return None
        suffix = digits[-3:]
        # Primary phone lookup
        candidates = self.db.query(Tenant).filter(Tenant.phone.contains(suffix)).all()
        for t in candidates:
            if _normalize_kz(_digits_only(t.phone)) == digits:
                return _tenant_to_info(t)
        # Fallback: emergency_contact lookup
        ec_candidates = (
            self.db.query(Tenant)
            .filter(Tenant.emergency_contact.isnot(None), Tenant.emergency_contact.contains(suffix))
            .all()
        )
        for t in ec_candidates:
            if _normalize_kz(_digits_only(t.emergency_contact)) == digits:
                return _tenant_to_info(t)
        return None

    def get_or_create_conversation(self, tenant_id: int, chat_id: str) -> ConversationSnapshot:
        conv = (
            self.db.query(Conversation)
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
                self.db.commit()
                self.db.refresh(conv)
            return _conv_to_snapshot(conv)

        conv = Conversation(
            tenant_id=tenant_id,
            whatsapp_chat_id=chat_id,
            status=ConversationStatusEnum.open,
            state=ConversationStateEnum.new_conversation,
        )
        self.db.add(conv)
        self.db.commit()
        self.db.refresh(conv)
        return _conv_to_snapshot(conv)

    def get_message_history(
        self, conversation_id: int, since: datetime | None = None,
    ) -> list[HistoryMessage]:
        query = self.db.query(Message).filter(Message.conversation_id == conversation_id)
        if since:
            query = query.filter(Message.created_at >= since)
        messages = query.order_by(Message.created_at.asc()).limit(20).all()
        result = []
        for m in messages:
            role = "tenant" if m.sender == MessageSenderEnum.tenant else "ai"
            if m.content:
                result.append(HistoryMessage(role=role, content=m.content))
        return result

    def save_message(
        self, conversation_id: int, sender: str, content: str,
        image_base64: str | None = None,
    ) -> None:
        sender_enum = MessageSenderEnum(sender)
        if image_base64:
            msg_type = MessageTypeEnum.image if not content or content == "[Фото]" else MessageTypeEnum.mixed
        else:
            msg_type = MessageTypeEnum.text
        msg = Message(
            conversation_id=conversation_id,
            sender=sender_enum,
            message_type=msg_type,
            content=content,
            media_url=image_base64,
        )
        self.db.add(msg)
        self.db.commit()

    def update_conversation(
        self, conversation_id: int, update: ConversationStateUpdate,
    ) -> None:
        conv = self.db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not conv:
            return

        if update.state is not None:
            conv.state = _STATE_TO_DB[update.state.value]
        if update.status is not None:
            conv.status = ConversationStatusEnum(update.status)
        if update.scenario is not None:
            conv.scenario = _SCENARIO_TO_DB.get(update.scenario)
        if update.confidence is not None:
            conv.classifier_confidence = update.confidence
        if update.escalated_at is not None:
            conv.escalated_at = update.escalated_at
        if update.reopened_at is not None:
            conv.reopened_at = update.reopened_at
        if update.context_data is not None:
            conv.context_data = update.context_data

        self.db.commit()


# ── SqlSchedulingService ─────────────────────────────────────────────────────

class SqlSchedulingService:
    def __init__(self, db: Session):
        self.db = db

    def find_available_slots(
        self, category: str, urgency: str, num_slots: int = 3,
    ) -> list[SlotInfo]:
        raw = scheduler_mod.find_available_slots(self.db, category, urgency, num_slots)
        return [SlotInfo(**s) for s in raw]

    def find_slots_for_date(
        self, category: str, target_date, exclude_ticket_id: int | None = None,
    ) -> list[SlotInfo]:
        raw = scheduler_mod.find_slots_for_date(self.db, category, target_date, exclude_ticket_id)
        return [SlotInfo(**s) for s in raw]

    def find_slot_for_time(
        self, category: str, target_date, hour: int, minute: int,
        exclude_ticket_id: int | None = None,
    ) -> list[SlotInfo]:
        raw = scheduler_mod.find_slot_for_time(self.db, category, target_date, hour, minute, exclude_ticket_id)
        return [SlotInfo(**s) for s in raw]

    def verify_slot_available(
        self, technician_id: str, start_iso: str, exclude_ticket_id: int | None = None,
    ) -> bool:
        return scheduler_mod.verify_slot_available(self.db, technician_id, start_iso, exclude_ticket_id)

    def create_ticket(
        self, tenant_id: int, context_data: dict, conversation_id: int,
    ) -> TicketResult:
        ticket = scheduler_mod.create_ticket_from_context(
            self.db, tenant_id, context_data, conversation_id,
        )
        return TicketResult(
            ticket_number=ticket.ticket_number,
            assigned_to=ticket.assigned_to,
            description=ticket.description,
            category=ticket.category,
            urgency=ticket.urgency,
        )

    def lookup_tenant_tickets(self, tenant_id: int) -> list[TicketSummary]:
        tickets = (
            self.db.query(models.Ticket)
            .filter(
                models.Ticket.tenant_id == tenant_id,
                models.Ticket.status.notin_([
                    models.TicketStatusEnum.done,
                    models.TicketStatusEnum.cancelled,
                ]),
            )
            .order_by(models.Ticket.created_at.desc())
            .limit(5)
            .all()
        )
        return [
            TicketSummary(
                ticket_number=t.ticket_number,
                category=t.category,
                status=t.status.value,
                description=(t.description[:80] + "...") if t.description and len(t.description) > 80 else t.description,
                scheduled_time=t.scheduled_time.isoformat() if t.scheduled_time else None,
                assigned_to_name=t.assignee.name if t.assignee else None,
                ticket_id=t.id,
            )
            for t in tickets
        ]

    def reschedule_ticket(
        self, ticket_number: str, tenant_id: int, technician_id: str, new_start_iso: str,
    ) -> TicketResult | None:
        ticket = (
            self.db.query(models.Ticket)
            .filter(
                models.Ticket.ticket_number == ticket_number,
                models.Ticket.tenant_id == tenant_id,
                models.Ticket.status.notin_([
                    models.TicketStatusEnum.done,
                    models.TicketStatusEnum.cancelled,
                ]),
            )
            .first()
        )
        if not ticket:
            return None
        from datetime import datetime
        new_dt = datetime.fromisoformat(new_start_iso).replace(tzinfo=None)
        ticket.scheduled_time = new_dt
        ticket.assigned_to = technician_id
        self.db.commit()
        self.db.refresh(ticket)
        return TicketResult(
            ticket_number=ticket.ticket_number,
            assigned_to=ticket.assigned_to,
            description=ticket.description,
            category=ticket.category,
            urgency=ticket.urgency,
        )

    def add_ticket_comment(
        self, ticket_number: str, tenant_id: int, comment: str,
    ) -> bool:
        ticket = (
            self.db.query(models.Ticket)
            .filter(
                models.Ticket.ticket_number == ticket_number,
                models.Ticket.tenant_id == tenant_id,
                models.Ticket.status.notin_([
                    models.TicketStatusEnum.done,
                    models.TicketStatusEnum.cancelled,
                ]),
            )
            .first()
        )
        if not ticket:
            return False
        agent_user = (
            self.db.query(models.User)
            .filter(models.User.role == models.RoleEnum.agent)
            .first()
        )
        note = models.TicketNote(
            ticket_id=ticket.id,
            author_id=agent_user.id if agent_user else None,
            text=comment,
        )
        self.db.add(note)
        self.db.commit()
        return True

    def cancel_ticket(self, ticket_number: str, tenant_id: int) -> tuple[bool, str]:
        ticket = (
            self.db.query(models.Ticket)
            .filter(
                models.Ticket.ticket_number == ticket_number,
                models.Ticket.tenant_id == tenant_id,
            )
            .first()
        )
        if not ticket:
            return False, "not_found"
        if ticket.status == models.TicketStatusEnum.cancelled:
            return False, "already_cancelled"
        if ticket.status == models.TicketStatusEnum.done:
            return False, "already_done"
        ticket.status = models.TicketStatusEnum.cancelled
        self.db.commit()
        return True, "ok"

    def find_technician_contact(self, category: str) -> dict | None:
        techs = scheduler_mod._find_all_techs(self.db)
        if techs:
            tech = techs[0]
            return {"name": tech.name, "phone": tech.phone or ""}
        return None


# ── WhatsAppNotificationService ──────────────────────────────────────────────

class WhatsAppNotificationService:
    def __init__(self, db: Session, llm: OpenAILLMClient):
        self.db = db
        self.llm = llm

    def send_reply(self, chat_id: str, text: str) -> None:
        notifier_mod.send_whatsapp_reply(chat_id, text)

    def escalate(
        self,
        tenant: TenantInfo,
        phone: str,
        last_message: str,
        history: list[HistoryMessage],
    ) -> None:
        display_phone = notifier_mod._format_phone(phone)

        # Group chat alert
        notifier_mod.send_escalation_alert(
            tenant_name=tenant.name,
            tenant_phone=phone,
            building_name=tenant.building_name,
            apartment=tenant.apartment,
            last_message=last_message,
        )

        # Personal dispatcher notifications
        history_dicts = [{"role": m.role, "content": m.content} for m in history]
        fallback = (
            "⚠️ *Требуется оператор*\n\n"
            f"*Жилец:* {tenant.name}\n"
            f"*Телефон:* {display_phone}\n"
            f"*Здание:* {tenant.building_name}, кв. {tenant.apartment}\n\n"
            f"*История:*\n" + "\n".join(
                f"{'Жилец' if m.role == 'tenant' else 'Бот'}: {m.content}"
                for m in history[-5:]
            )
        )

        user_content = (
            f"TENANT DETAILS:\n"
            f"Name: {tenant.name}\n"
            f"Phone: {display_phone}\n"
            f"Building: {tenant.building_name}\n"
            f"Apartment: {tenant.apartment}\n\n"
            f"CONVERSATION HISTORY:\n"
            + "\n".join(
                f"{'Tenant' if m.role == 'tenant' else 'AI'}: {m.content}"
                for m in history[-10:]
            )
        )

        message = self.llm.generate_message("escalation", user_content, fallback)
        notifier_mod.notify_dispatchers(self.db, message)

    def notify_technician_assigned(
        self,
        technician_name: str,
        ticket_number: str,
        tenant: TenantInfo,
        description: str,
        category: str,
        urgency: str,
        scheduled_time: str,
    ) -> bool:
        try:
            fallback = (
                f"🔧 *Новая заявка: {ticket_number}*\n\n"
                f"*Жилец:* {tenant.name}\n"
                f"*Адрес:* {tenant.building_name}, кв. {tenant.apartment}\n"
                f"*Проблема:* {description}\n"
                f"*Категория:* {category}\n"
                f"*Срочность:* {urgency}\n"
                f"*Время визита:* {scheduled_time}\n\n"
                "При возникновении вопросов свяжитесь с диспетчером."
            )

            user_content = (
                f"TICKET DETAILS:\n"
                f"Ticket number: {ticket_number}\n"
                f"Technician name: {technician_name}\n"
                f"Tenant name: {tenant.name}\n"
                f"Building: {tenant.building_name}\n"
                f"Apartment: {tenant.apartment}\n"
                f"Problem: {description}\n"
                f"Category: {category}\n"
                f"Urgency: {urgency}\n"
                f"Scheduled time: {scheduled_time}"
            )

            message = self.llm.generate_message("technician_assignment", user_content, fallback)

            # Find technician by matching offered slot technician_name
            # The ticket's assigned_to is already set — look up via notifier
            techs = (
                self.db.query(models.User)
                .filter(models.User.name == technician_name, models.User.role == models.RoleEnum.technician)
                .all()
            )
            sent = False
            for tech in techs:
                if tech.phone:
                    digits = notifier_mod._normalize_phone(tech.phone)
                    if digits:
                        chat_id = f"{digits}@c.us"
                        notifier_mod.send_whatsapp_reply(chat_id, message)
                        sent = True
            return sent
        except Exception:
            logger.exception("Failed to notify technician %s for ticket %s", technician_name, ticket_number)
            return False


# ── Factory ──────────────────────────────────────────────────────────────────

_llm_client: OpenAILLMClient | None = None


def _get_llm_client() -> OpenAILLMClient:
    global _llm_client
    if _llm_client is None:
        api_key = os.getenv("OPENAI_TOKEN", "")
        prompts_dir = Path(__file__).resolve().parent.parent.parent / "prompts"
        _llm_client = OpenAILLMClient(api_key=api_key, prompts_dir=prompts_dir)
    return _llm_client


def create_agent_engine(db: Session) -> AgentEngine:
    """Create a fully-wired AgentEngine with SQL-backed tools."""
    llm = _get_llm_client()
    prompts_dir = Path(__file__).resolve().parent.parent.parent / "prompts"
    return AgentEngine(
        store=SqlConversationStore(db),
        scheduler=SqlSchedulingService(db),
        notifier=WhatsAppNotificationService(db, llm),
        llm=llm,
        prompts_dir=prompts_dir,
    )
