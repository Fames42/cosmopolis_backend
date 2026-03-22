"""Conversation orchestrator — thin compatibility shim delegating to agent package."""

from sqlalchemy.orm import Session

from ..agent.types import AgentResult
from ..agent.context import ConversationContext
from .adapters import create_agent_engine

# Re-export AgentResult as AgentResponse for backward compatibility
AgentResponse = AgentResult


def save_incoming_message(
    db: Session, phone: str, content: str, *, image_base64: str | None = None,
):
    """Save an incoming tenant message to DB without processing it."""
    engine = create_agent_engine(db)
    return engine.save_incoming_message(phone, content, image_base64=image_base64)


def process_conversation(db: Session, conv_snapshot, tenant_info, phone: str):
    """Process a conversation through the agent pipeline."""
    engine = create_agent_engine(db)
    ctx = ConversationContext(conv_snapshot, tenant_info, phone)
    return engine.process_conversation(ctx)


def handle_message(
    db: Session, phone: str, content: str, *, image_base64: str | None = None,
) -> tuple[str, str, AgentResult | None]:
    """Process an incoming message and return (reply, state, agent_response)."""
    engine = create_agent_engine(db)
    tenant, snapshot, chat_id = engine.save_incoming_message(phone, content, image_base64=image_base64)
    if not tenant:
        return (
            "Извините, ваш номер не найден в системе. Обратитесь в управляющую компанию для регистрации.",
            "unknown_tenant",
            None,
        )
    ctx = ConversationContext(snapshot, tenant, phone)
    return engine.process_conversation(ctx)
