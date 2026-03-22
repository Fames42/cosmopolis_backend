"""Interfaces the backend must implement to provide tools to the agent."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, Any

from .types import (
    TenantInfo,
    HistoryMessage,
    SlotInfo,
    TicketResult,
    TicketSummary,
    ConversationSnapshot,
    ConversationStateUpdate,
)


class ConversationStore(Protocol):
    """Persistence layer for tenants, conversations, and messages."""

    def find_tenant_by_phone(self, phone: str) -> TenantInfo | None: ...

    def get_or_create_conversation(self, tenant_id: int, chat_id: str) -> ConversationSnapshot: ...

    def get_message_history(
        self, conversation_id: int, since: datetime | None = None,
    ) -> list[HistoryMessage]: ...

    def save_message(
        self, conversation_id: int, sender: str, content: str,
        image_base64: str | None = None,
    ) -> None: ...

    def update_conversation(
        self, conversation_id: int, update: ConversationStateUpdate,
    ) -> None: ...


class SchedulingService(Protocol):
    """Technician slot queries and ticket creation."""

    def find_available_slots(
        self, category: str, urgency: str, num_slots: int = 3,
    ) -> list[SlotInfo]: ...

    def find_slots_for_date(
        self, category: str, target_date: date,
        exclude_ticket_id: int | None = None,
    ) -> list[SlotInfo]: ...

    def find_slot_for_time(
        self, category: str, target_date: date, hour: int, minute: int,
        exclude_ticket_id: int | None = None,
    ) -> list[SlotInfo]: ...

    def verify_slot_available(
        self, technician_id: str, start_iso: str,
    ) -> bool: ...

    def create_ticket(
        self, tenant_id: int, context_data: dict, conversation_id: int,
    ) -> TicketResult: ...

    def lookup_tenant_tickets(self, tenant_id: int) -> list[TicketSummary]: ...

    def reschedule_ticket(
        self, ticket_number: str, tenant_id: int, technician_id: str, new_start_iso: str,
    ) -> TicketResult | None: ...

    def add_ticket_comment(
        self, ticket_number: str, tenant_id: int, comment: str,
    ) -> bool: ...

    def cancel_ticket(self, ticket_number: str, tenant_id: int) -> bool: ...


class NotificationService(Protocol):
    """Outbound messaging (WhatsApp, alerts)."""

    def send_reply(self, chat_id: str, text: str) -> None: ...

    def escalate(
        self,
        tenant: TenantInfo,
        phone: str,
        last_message: str,
        history: list[HistoryMessage],
    ) -> None: ...

    def notify_technician_assigned(
        self,
        technician_name: str,
        ticket_number: str,
        tenant: TenantInfo,
        description: str,
        category: str,
        urgency: str,
        scheduled_time: str,
    ) -> None: ...


class LLMClient(Protocol):
    """LLM calls — agentic loop via Responses API + utility generation."""

    def run(
        self,
        user_message: str,
        instructions: str,
        tools: list[dict],
        previous_response_id: str | None = None,
    ) -> tuple[str | None, list[dict], str | None]:
        """Run the Responses API agentic call.

        Returns:
            (reply_text, tool_calls, response_id)
            - reply_text: final text output from the model (None if only tool calls)
            - tool_calls: list of {"call_id", "name", "arguments"} dicts
            - response_id: response ID for continuity
        """
        ...

    def submit_tool_outputs(
        self,
        tool_outputs: list[dict],
        previous_response_id: str,
        instructions: str,
        tools: list[dict],
    ) -> tuple[str | None, list[dict], str | None]:
        """Submit tool call results and get next response.

        Returns same tuple as run().
        """
        ...

    def generate_message(self, prompt_name: str, user_content: str, fallback: str) -> str:
        """Generic LLM message generation (for escalation, assignment, etc.)."""
        ...
