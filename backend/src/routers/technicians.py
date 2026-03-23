from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime, timezone, date, timedelta

from .. import models, schemas
from ..database import get_db
from ..auth import get_current_user, get_dispatcher_user, check_role

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
    # Single query: technicians + active ticket count via LEFT JOIN
    active_tickets_subq = (
        db.query(
            models.Ticket.assigned_to,
            func.count(models.Ticket.id).label("active_count"),
        )
        .filter(models.Ticket.status != models.TicketStatusEnum.done)
        .group_by(models.Ticket.assigned_to)
        .subquery()
    )
    techs = (
        db.query(models.User, active_tickets_subq.c.active_count)
        .outerjoin(active_tickets_subq, models.User.id == active_tickets_subq.c.assigned_to)
        .filter(models.User.role == models.RoleEnum.technician)
        .all()
    )
    return [
        {
            "id": t.id,
            "name": t.name,
            "email": t.email or "",
            "phone": t.phone or "",
            "is_head": t.is_head or False,
            "activeTickets": count or 0,
            "status": "ACTIVE",
        }
        for t, count in techs
    ]

@router.get("/schedules", response_model=List[schemas.TechnicianScheduleOverview])
def get_all_technician_schedules(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_dispatcher_user),
):
    """Get all technicians' weekly schedules. Dispatcher/admin only."""
    techs = (
        db.query(models.User)
        .filter(models.User.role == models.RoleEnum.technician)
        .all()
    )
    result = []
    for tech in techs:
        schedules = (
            db.query(models.TechnicianSchedule)
            .filter(models.TechnicianSchedule.technician_id == tech.id)
            .order_by(models.TechnicianSchedule.day_of_week)
            .all()
        )
        result.append(schemas.TechnicianScheduleOverview(
            technician_id=tech.id,
            technician_name=tech.name,
            schedules=[
                schemas.TechnicianScheduleItem(
                    day_of_week=s.day_of_week,
                    start_time=s.start_time,
                    end_time=s.end_time,
                )
                for s in schedules
            ],
        ))
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
        role=models.RoleEnum.technician,
        is_head=tech.is_head,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {
        "id": new_user.id,
        "name": new_user.name,
        "email": new_user.email,
        "phone": new_user.phone or "",
        "is_head": new_user.is_head or False,
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
    if tech_update.is_head is not None:
        tech.is_head = tech_update.is_head

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
        "is_head": tech.is_head or False,
        "activeTickets": active_count,
        "status": "ACTIVE"
    }

@router.delete("/{tech_id}", status_code=200)
def delete_technician(
    tech_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if current_user.role != models.RoleEnum.admin:
        raise HTTPException(status_code=403, detail="Only admins can delete technicians")
    tech = db.query(models.User).filter(
        models.User.id == tech_id,
        models.User.role == models.RoleEnum.technician,
    ).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Техник не найден")

    # Unassign tickets
    db.query(models.Ticket).filter(models.Ticket.assigned_to == tech_id).update(
        {"assigned_to": None}
    )
    # Remove notes authored by this technician
    db.query(models.TicketNote).filter(models.TicketNote.author_id == tech_id).delete()
    # Remove schedule
    db.query(models.TechnicianSchedule).filter(
        models.TechnicianSchedule.technician_id == tech_id
    ).delete()

    db.delete(tech)
    db.commit()
    return {"detail": "Техник удалён"}


@router.get("/me/schedule", response_model=List[schemas.TechnicianScheduleResponse])
def get_my_schedule(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get the current technician's own weekly schedule."""
    schedules = (
        db.query(models.TechnicianSchedule)
        .filter(models.TechnicianSchedule.technician_id == current_user.id)
        .order_by(models.TechnicianSchedule.day_of_week)
        .all()
    )
    return schedules


@router.put("/me/schedule", response_model=List[schemas.TechnicianScheduleResponse])
def set_my_schedule(
    body: schemas.TechnicianScheduleBulkUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Bulk-replace the current technician's own weekly schedule."""
    db.query(models.TechnicianSchedule).filter(
        models.TechnicianSchedule.technician_id == current_user.id,
    ).delete()

    new_schedules = []
    for item in body.schedules:
        s = models.TechnicianSchedule(
            technician_id=current_user.id,
            day_of_week=item.day_of_week,
            start_time=item.start_time,
            end_time=item.end_time,
        )
        db.add(s)
        new_schedules.append(s)

    db.commit()
    for s in new_schedules:
        db.refresh(s)
    return new_schedules


@router.get("/{tech_id}/schedule", response_model=List[schemas.TechnicianScheduleResponse])
def get_technician_schedule(
    tech_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_dispatcher_user),
):
    tech = db.query(models.User).filter(
        models.User.id == tech_id,
        models.User.role == models.RoleEnum.technician,
    ).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Техник не найден")

    schedules = (
        db.query(models.TechnicianSchedule)
        .filter(models.TechnicianSchedule.technician_id == tech_id)
        .order_by(models.TechnicianSchedule.day_of_week)
        .all()
    )
    return schedules


@router.put("/{tech_id}/schedule", response_model=List[schemas.TechnicianScheduleResponse])
def set_technician_schedule(
    tech_id: str,
    body: schemas.TechnicianScheduleBulkUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_dispatcher_user),
):
    tech = db.query(models.User).filter(
        models.User.id == tech_id,
        models.User.role == models.RoleEnum.technician,
    ).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Техник не найден")

    # Delete existing schedule
    db.query(models.TechnicianSchedule).filter(
        models.TechnicianSchedule.technician_id == tech_id,
    ).delete()

    # Insert new rows
    new_schedules = []
    for item in body.schedules:
        s = models.TechnicianSchedule(
            technician_id=tech_id,
            day_of_week=item.day_of_week,
            start_time=item.start_time,
            end_time=item.end_time,
        )
        db.add(s)
        new_schedules.append(s)

    db.commit()
    for s in new_schedules:
        db.refresh(s)
    return new_schedules


@router.get("/{tech_id}/workload", response_model=schemas.TechnicianWorkloadResponse)
def get_technician_workload(
    tech_id: str,
    date_from: Optional[date] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[date] = Query(None, description="End date (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_dispatcher_user),
):
    """Get a technician's assigned tickets, optionally filtered by date range. Dispatcher/admin only."""
    tech = db.query(models.User).filter(
        models.User.id == tech_id,
        models.User.role == models.RoleEnum.technician,
    ).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Техник не найден")

    query = db.query(models.Ticket).filter(models.Ticket.assigned_to == tech_id)

    if date_from:
        query = query.filter(models.Ticket.scheduled_time >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(models.Ticket.scheduled_time < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))

    tickets = query.order_by(models.Ticket.scheduled_time.asc().nullslast()).all()

    return schemas.TechnicianWorkloadResponse(
        technician_id=tech.id,
        technician_name=tech.name,
        tickets=[
            schemas.TechnicianWorkloadItem(
                ticket_number=t.ticket_number,
                category=t.category or "General",
                urgency=t.urgency or "low",
                status=t.status.value if t.status else "new",
                scheduled_time=t.scheduled_time,
                description=t.description or "",
            )
            for t in tickets
        ],
    )


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
