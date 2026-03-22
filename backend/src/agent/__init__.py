"""Self-contained AI agent package — no SQLAlchemy or backend dependencies."""

from .types import (
    ConversationState,
    Scenario,
    TenantInfo,
    HistoryMessage,
    SlotInfo,
    TicketResult,
    ConversationSnapshot,
    ConversationStateUpdate,
    AgentResult,
)
from .context import ConversationContext
from .engine import AgentEngine
from .protocols import ConversationStore, SchedulingService, NotificationService, LLMClient

__all__ = [
    "AgentEngine",
    "ConversationContext",
    "ConversationState",
    "Scenario",
    "TenantInfo",
    "HistoryMessage",
    "SlotInfo",
    "TicketResult",
    "ConversationSnapshot",
    "ConversationStateUpdate",
    "AgentResult",
    "ConversationStore",
    "SchedulingService",
    "NotificationService",
    "LLMClient",
]
