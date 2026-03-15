from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from .. import models, schemas
from ..database import get_db
from ..auth import get_current_user, check_role

router = APIRouter()

def format_ticket_list(ticket: models.Ticket) -> schemas.TicketDispatcherListResponse:
    if ticket.tenant:
        building_name = ticket.tenant.building.name if ticket.tenant.building else "Unknown Building"
        tenant_str = f"Apt {ticket.tenant.apartment} ({building_name})"
    else:
        tenant_str = "Unknown"
    assigned_to_name = ticket.assignee.name if ticket.assignee else None
    
    return schemas.TicketDispatcherListResponse(
        id=ticket.ticket_number,
        category=ticket.category or "General",
        urgency=ticket.urgency or "LOW",
        tenant=tenant_str,
        assignedTo=assigned_to_name,
        status=ticket.status.value.upper() if ticket.status else "NEW",
        scheduled=ticket.scheduled_time.isoformat() if ticket.scheduled_time else None,
        created=ticket.created_at.date().isoformat() if ticket.created_at else ""
    )

def format_ticket_detail(ticket: models.Ticket) -> schemas.TicketDispatcherDetailResponse:
    tenant_info = schemas.TenantInfoSchema(
        name=ticket.tenant.name if ticket.tenant else "N/A",
        phone=ticket.tenant.phone if ticket.tenant else "N/A",
        address=f"{ticket.tenant.building.address}, {ticket.tenant.building.name}" if ticket.tenant and ticket.tenant.building else "N/A",
        apartment=f"Apt {ticket.tenant.apartment}" if ticket.tenant else "N/A"
    )
    issue_details = schemas.IssueDetailsSchema(
        category=ticket.category or "General",
        urgency=ticket.urgency or "LOW",
        description=ticket.description or "",
        photo_urls=ticket.photo_urls,
    )
    notes = [
        schemas.TicketNoteSchema(
            id=note.id,
            author=note.author.name if note.author else "Unknown",
            time=note.created_at.isoformat() if note.created_at else "",
            text=note.text,
            role=note.author.role.value if note.author else "unknown"
        )
        for note in ticket.notes
    ]
    return schemas.TicketDispatcherDetailResponse(
        id=ticket.ticket_number,
        ticketStatus=ticket.status.value.upper() if ticket.status else "NEW",
        assignedTech=ticket.assignee.name if ticket.assignee else None,
        scheduledDate=ticket.scheduled_time.isoformat() if ticket.scheduled_time else None,
        created=ticket.created_at.date().isoformat() if ticket.created_at else "",
        tenantInfo=tenant_info,
        issueDetails=issue_details,
        notes=notes
    )

@router.get("", response_model=List[schemas.TicketDispatcherListResponse])
def read_tickets(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    tickets = db.query(models.Ticket).offset(skip).limit(limit).all()
    return [format_ticket_list(t) for t in tickets]

@router.get("/{ticket_id}", response_model=schemas.TicketDispatcherDetailResponse)
def read_ticket(
    ticket_id: str, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    ticket = db.query(models.Ticket).filter(models.Ticket.ticket_number == ticket_id).first()
    if not ticket:
        if ticket_id.isdigit():
            ticket = db.query(models.Ticket).filter(models.Ticket.id == int(ticket_id)).first()
    
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    return format_ticket_detail(ticket)

@router.post("", response_model=schemas.TicketDispatcherDetailResponse, dependencies=[Depends(check_role([models.RoleEnum.admin, models.RoleEnum.dispatcher]))])
def create_ticket(ticket: schemas.TicketCreate, db: Session = Depends(get_db)):
    import uuid
    db_ticket = models.Ticket(**ticket.model_dump(), ticket_number=f"TKT-{str(uuid.uuid4())[:8].upper()}")
    db.add(db_ticket)
    db.commit()
    db.refresh(db_ticket)
    return format_ticket_detail(db_ticket)

@router.put("/{ticket_id}", response_model=schemas.TicketDispatcherDetailResponse)
def update_ticket(
    ticket_id: str, 
    ticket_update: dict,  
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    ticket = db.query(models.Ticket).filter(models.Ticket.ticket_number == ticket_id).first()
    if not ticket:
        if ticket_id.isdigit():
            ticket = db.query(models.Ticket).filter(models.Ticket.id == int(ticket_id)).first()
            
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    if "status" in ticket_update:
        status_str = ticket_update["status"].lower()
        try:
            ticket.status = models.TicketStatusEnum(status_str)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {ticket_update['status']}")
    
    if "assignedTo" in ticket_update:
        assignee_name = ticket_update["assignedTo"]
        if assignee_name:
            user = db.query(models.User).filter(models.User.name == assignee_name).first()
            if user:
                ticket.assigned_to = user.id
            else:
                 user = db.query(models.User).filter(models.User.id == assignee_name).first()
                 if user:
                     ticket.assigned_to = user.id
        else:
            ticket.assigned_to = None
            
    if "scheduledDate" in ticket_update:
        from datetime import datetime
        try:
            date_str = ticket_update["scheduledDate"]
            if date_str:
                date_str = date_str.replace("Z", "+00:00")
                ticket.scheduled_time = datetime.fromisoformat(date_str)
            else:
                ticket.scheduled_time = None
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid date format: {ticket_update['scheduledDate']}")

    if "urgency" in ticket_update:
        allowed = {"low", "medium", "high", "emergency"}
        urgency_str = ticket_update["urgency"].upper()
        if urgency_str.lower() not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid urgency: {ticket_update['urgency']}. Allowed: {', '.join(allowed)}",
            )
        ticket.urgency = urgency_str

    db.commit()
    db.refresh(ticket)
    return format_ticket_detail(ticket)

@router.post("/{ticket_id}/notes", response_model=schemas.TicketNoteSchema)
def add_note(
    ticket_id: str,
    note: schemas.NoteCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    ticket = db.query(models.Ticket).filter(models.Ticket.ticket_number == ticket_id).first()
    if not ticket:
        if ticket_id.isdigit():
            ticket = db.query(models.Ticket).filter(models.Ticket.id == int(ticket_id)).first()
            
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    new_note = models.TicketNote(
        ticket_id=ticket.id,
        author_id=current_user.id,
        text=note.text
    )
    db.add(new_note)
    db.commit()
    db.refresh(new_note)

    return schemas.TicketNoteSchema(
        id=new_note.id,
        author=current_user.name,
        time=new_note.created_at.isoformat(),
        text=new_note.text,
        role=current_user.role.value
    )


@router.get("/{ticket_id}/photo")
def get_ticket_photo(
    ticket_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get photos attached to a ticket. Returns {photo_urls: ["data:image/...;base64,..."]} or 404."""
    ticket = db.query(models.Ticket).filter(models.Ticket.ticket_number == ticket_id).first()
    if not ticket:
        if ticket_id.isdigit():
            ticket = db.query(models.Ticket).filter(models.Ticket.id == int(ticket_id)).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if not ticket.photo_urls:
        raise HTTPException(status_code=404, detail="No photos attached to this ticket")
    return {"photo_urls": ticket.photo_urls}
