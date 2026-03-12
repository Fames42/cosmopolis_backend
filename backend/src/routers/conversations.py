from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import List

from .. import models, schemas
from ..database import get_db
from ..auth import get_current_user

router = APIRouter()

# Read-only access for owners, admins, dispatchers
@router.get("", response_model=List[schemas.ConversationResponse])
def read_conversations(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    if current_user.role == models.RoleEnum.technician:
        raise HTTPException(status_code=403, detail="Technicians cannot view WhatsApp conversations")

    conversations = db.query(models.Conversation).order_by(desc(models.Conversation.created_at)).offset(skip).limit(limit).all()
    return conversations

@router.get("/{conversation_id}", response_model=schemas.ConversationResponse)
def read_conversation(
    conversation_id: int, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    if current_user.role == models.RoleEnum.technician:
        raise HTTPException(status_code=403, detail="Technicians cannot view WhatsApp conversations")

    conversation = db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    return conversation
