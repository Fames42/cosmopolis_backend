from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timezone

from .. import models, schemas
from ..database import get_db
from ..auth import get_current_user, check_role

router = APIRouter()

def format_tech_list(ticket: models.Ticket) -> schemas.TicketTechnicianListResponse:
    is_today = False
    scheduled_str = None
    if ticket.scheduled_time:
        is_today = ticket.scheduled_time.date() == datetime.now(timezone.utc).date()
        scheduled_str = ticket.scheduled_time.strftime("%b %d, %H:%M")
        
    if ticket.tenant:
        building_name = ticket.tenant.building.name if ticket.tenant.building else "Unknown Building"
        address_str = f"Apt {ticket.tenant.apartment}, {building_name}"
    else:
        address_str = "N/A"
        
    return schemas.TicketTechnicianListResponse(
        id=ticket.ticket_number,
        category=ticket.category or "General",
        address=address_str,
        urgency=ticket.urgency or "LOW",
        scheduled=scheduled_str,
        status=ticket.status.value.upper() if ticket.status else "NEW",
        isToday=is_today
    )

def format_tech_detail(ticket: models.Ticket) -> schemas.TicketTechnicianDetailResponse:
    if ticket.tenant:
        building_name = ticket.tenant.building.name if ticket.tenant.building else "Unknown Building"
        address_str = f"Apt {ticket.tenant.apartment}, {building_name}"
    else:
        address_str = "N/A"
        
    return schemas.TicketTechnicianDetailResponse(
        id=ticket.ticket_number,
        category=ticket.category or "General",
        urgency=ticket.urgency or "LOW",
        address=address_str,
        description=ticket.description or "",
        tenantPhone=ticket.tenant.phone if ticket.tenant else "N/A",
        status=ticket.status.value.upper() if ticket.status else "NEW",
        comments=[
            schemas.TicketCommentSchema(id=note.id, text=note.text)
            for note in ticket.notes
        ]
    )

@router.get("", response_model=List[schemas.TechnicianResponse])
def get_technicians(
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    techs = db.query(models.User).filter(models.User.role == models.RoleEnum.technician).all()
    result = []
    for t in techs:
        active_count = db.query(models.Ticket).filter(
            models.Ticket.assigned_to == t.id,
            models.Ticket.status != models.TicketStatusEnum.done
        ).count()
        result.append({
            "id": t.id,
            "name": t.name,
            "email": t.email or "",
            "phone": t.phone or "",
            "activeTickets": active_count,
            "status": "ACTIVE"
        })
    return result

@router.post("", response_model=schemas.TechnicianResponse)
def create_technician(
    tech: schemas.TechnicianCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    from ..auth import get_password_hash
    existing = db.query(models.User).filter(models.User.email == tech.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email уже зарегистрирован")
    new_user = models.User(
        name=tech.name,
        email=tech.email,
        phone=tech.phone,
        password_hash=get_password_hash(tech.password),
        role=models.RoleEnum.technician
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {
        "id": new_user.id,
        "name": new_user.name,
        "email": new_user.email,
        "phone": new_user.phone or "",
        "activeTickets": 0,
        "status": "ACTIVE"
    }

@router.put("/{tech_id}", response_model=schemas.TechnicianResponse)
def update_technician(
    tech_id: str,
    tech_update: schemas.TechnicianUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    tech = db.query(models.User).filter(
        models.User.id == tech_id,
        models.User.role == models.RoleEnum.technician
    ).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Техник не найден")

    if tech_update.name is not None:
        tech.name = tech_update.name
    if tech_update.email is not None:
        existing = db.query(models.User).filter(
            models.User.email == tech_update.email,
            models.User.id != tech_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email уже зарегистрирован")
        tech.email = tech_update.email
    if tech_update.phone is not None:
        tech.phone = tech_update.phone

    db.commit()
    db.refresh(tech)

    active_count = db.query(models.Ticket).filter(
        models.Ticket.assigned_to == tech.id,
        models.Ticket.status != models.TicketStatusEnum.done
    ).count()

    return {
        "id": tech.id,
        "name": tech.name,
        "email": tech.email or "",
        "phone": tech.phone or "",
        "activeTickets": active_count,
        "status": "ACTIVE"
    }

@router.get("/me/tickets", response_model=List[schemas.TicketTechnicianListResponse])
def get_my_tickets(
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    tickets = db.query(models.Ticket).filter(models.Ticket.assigned_to == current_user.id).all()
    return [format_tech_list(t) for t in tickets]

@router.get("/me/tickets/{ticket_id}", response_model=schemas.TicketTechnicianDetailResponse)
def get_my_ticket(
    ticket_id: str, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    ticket = db.query(models.Ticket).filter(
        models.Ticket.ticket_number == ticket_id,
        models.Ticket.assigned_to == current_user.id
    ).first()
    
    if not ticket:
        if ticket_id.isdigit():
            ticket = db.query(models.Ticket).filter(
                models.Ticket.id == int(ticket_id),
                models.Ticket.assigned_to == current_user.id
            ).first()
            
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found or not assigned to you")
    
    return format_tech_detail(ticket)

@router.post("/me/tickets/{ticket_id}/comments", response_model=schemas.TicketCommentSchema)
def add_my_ticket_comment(
    ticket_id: str,
    note: schemas.NoteCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    ticket = db.query(models.Ticket).filter(
        models.Ticket.ticket_number == ticket_id,
        models.Ticket.assigned_to == current_user.id
    ).first()

    if not ticket:
        if ticket_id.isdigit():
            ticket = db.query(models.Ticket).filter(
                models.Ticket.id == int(ticket_id),
                models.Ticket.assigned_to == current_user.id
            ).first()

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found or not assigned to you")

    new_note = models.TicketNote(
        ticket_id=ticket.id,
        author_id=current_user.id,
        text=note.text
    )
    db.add(new_note)
    db.commit()
    db.refresh(new_note)

    return schemas.TicketCommentSchema(id=new_note.id, text=new_note.text)

@router.put("/me/tickets/{ticket_id}/status", response_model=schemas.TicketTechnicianDetailResponse)
def update_my_ticket_status(
    ticket_id: str, 
    status_update: schemas.TicketUpdateStatus,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    ticket = db.query(models.Ticket).filter(
        models.Ticket.ticket_number == ticket_id,
        models.Ticket.assigned_to == current_user.id
    ).first()
    
    if not ticket:
        if ticket_id.isdigit():
            ticket = db.query(models.Ticket).filter(
                models.Ticket.id == int(ticket_id),
                models.Ticket.assigned_to == current_user.id
            ).first()
            
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found or not assigned to you")

    status_str = status_update.status.lower()
    try:
        ticket.status = models.TicketStatusEnum(status_str)
        db.commit()
        db.refresh(ticket)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid status")
        
    return format_tech_detail(ticket)
