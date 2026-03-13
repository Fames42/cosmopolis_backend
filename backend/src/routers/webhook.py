import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import TestMessageRequest, TestMessageResponse, AgentResponse
from ..services.orchestrator import handle_message

logger = logging.getLogger("uvicorn.error")

router = APIRouter()


@router.post("/test", response_model=TestMessageResponse)
def test_webhook(req: TestMessageRequest, db: Session = Depends(get_db)):
    """Test endpoint to simulate an incoming WhatsApp message.

    Send a phone number and message text, get back the AI reply,
    conversation state, and agent response (with classification if available).
    """
    reply, state, agent_resp = handle_message(db, req.phone, req.message)

    return TestMessageResponse(
        reply=reply,
        state=state,
        agent_response=agent_resp,
    )
