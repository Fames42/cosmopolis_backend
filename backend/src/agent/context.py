"""Per-conversation isolated state container."""

from __future__ import annotations

from datetime import datetime, timezone

from .types import (
    ConversationSnapshot,
    ConversationState,
    ConversationStateUpdate,
    TenantInfo,
)


class ConversationContext:
    """Created fresh for each process_conversation call — no shared mutable state."""

    def __init__(self, snapshot: ConversationSnapshot, tenant: TenantInfo, phone: str):
        self.conversation_id = snapshot.id
        self.tenant = tenant
        self.phone = phone
        self.chat_id = snapshot.chat_id

        # Mutable working copies — isolated to this invocation
        self.state = snapshot.state
        self.status = snapshot.status
        self.scenario = snapshot.scenario
        self.context_data: dict = dict(snapshot.context_data or {})
        self.escalated_at = snapshot.escalated_at
        self.reopened_at = snapshot.reopened_at

    def update_context(self, updates: dict) -> None:
        self.context_data.update(updates)

    def reset_for_new_topic(self) -> None:
        self.context_data = {}
        self.scenario = None
        self.reopened_at = datetime.now(timezone.utc)

    def to_state_update(self) -> ConversationStateUpdate:
        return ConversationStateUpdate(
            state=self.state,
            status=self.status,
            scenario=self.scenario,
            escalated_at=self.escalated_at,
            reopened_at=self.reopened_at,
            context_data=self.context_data,
        )
