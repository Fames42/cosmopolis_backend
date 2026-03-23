from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Any
from sqlalchemy import func

from .. import models
from ..database import get_db
from ..auth import get_owner_user

router = APIRouter()

@router.get("/summary", dependencies=[Depends(get_owner_user)])
def get_analytics_summary(db: Session = Depends(get_db)) -> Dict[str, Any]:
    # Total tickets
    total_tickets = db.query(models.Ticket).count()
    
    # Tickets by status — always include all statuses (0 if none)
    status_counts = db.query(models.Ticket.status, func.count(models.Ticket.id)).group_by(models.Ticket.status).all()
    status_dict = {s.value: 0 for s in models.TicketStatusEnum}
    for status, count in status_counts:
        status_dict[status.value] = count

    # active conversations
    open_conversations = db.query(models.Conversation).filter(models.Conversation.status == models.ConversationStatusEnum.open).count()
    
    return {
        "total_tickets": total_tickets,
        "tickets_by_status": status_dict,
        "open_conversations": open_conversations
    }
