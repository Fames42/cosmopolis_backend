"""Agent-owned data types — no SQLAlchemy, no backend imports."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel


# ── Enums (mirrors of DB enums, owned by agent) ─────────────────────────────

class ConversationState(str, enum.Enum):
    new_conversation = "new_conversation"
    gathering = "gathering"
    classified_service = "classified_service"
    classified_faq = "classified_faq"
    classified_billing = "classified_billing"
    classified_announcement = "classified_announcement"
    service_collecting_details = "service_collecting_details"
    service_assessing_urgency = "service_assessing_urgency"
    service_scheduling = "service_scheduling"
    service_ready_for_ticket = "service_ready_for_ticket"
    ticket_created = "ticket_created"
    technician_assigned = "technician_assigned"
    managing_ticket = "managing_ticket"
    escalated_to_human = "escalated_to_human"
    closed = "closed"


class Scenario(str, enum.Enum):
    service = "service"
    faq = "faq"
    billing = "billing"
    announcement = "announcement"
    unknown = "unknown"


# ── Data transfer objects ────────────────────────────────────────────────────

@dataclass(frozen=True)
class TenantInfo:
    id: int
    name: str
    phone: str
    building_name: str
    apartment: str
    agent_enabled: bool
    building_address: str = ""
    building_house_number: str = ""
    building_floor: str = ""
    building_block: str = ""
    building_actual_number: str = ""
    building_legal_number: str = ""


@dataclass(frozen=True)
class HistoryMessage:
    role: str   # "tenant" | "ai"
    content: str


@dataclass(frozen=True)
class SlotInfo:
    technician_id: str
    technician_name: str
    start: str   # ISO datetime
    end: str     # ISO datetime


@dataclass(frozen=True)
class TicketResult:
    ticket_number: str
    assigned_to: str | None
    description: str | None = None
    category: str | None = None
    urgency: str | None = None


@dataclass
class ConversationSnapshot:
    id: int
    tenant_id: int
    chat_id: str
    status: str          # "open" | "closed"
    state: ConversationState
    scenario: str | None
    context_data: dict
    escalated_at: datetime | None
    reopened_at: datetime | None


@dataclass
class ConversationStateUpdate:
    state: ConversationState | None = None
    status: str | None = None
    scenario: str | None = None
    confidence: float | None = None
    escalated_at: datetime | None = None
    reopened_at: datetime | None = None
    context_data: dict | None = None


@dataclass(frozen=True)
class TicketSummary:
    ticket_number: str
    category: str | None
    status: str
    description: str | None
    scheduled_time: str | None
    assigned_to_name: str | None
    ticket_id: int | None = None


class AgentResult(BaseModel):
    reply: str
    classified: bool = False
    scenario: Optional[str] = None
    confidence: Optional[float] = None
    subtype: Optional[str] = None
    requires_human: bool = False
    tools_called: List[dict] = []
