from io import BytesIO
from datetime import date, datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import case
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from openpyxl import Workbook
from openpyxl.styles import Font

from .. import models, schemas
from ..database import get_db
from ..auth import get_dispatcher_user
from ..services import notifier, scheduler

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


def _parse_datetime(value: str | None, field_name: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {value}") from None


def _find_technician(db: Session, identifier: str) -> models.User:
    user = (
        db.query(models.User)
        .filter(models.User.id == identifier, models.User.role == models.RoleEnum.technician)
        .first()
    )
    if not user:
        user = (
            db.query(models.User)
            .filter(models.User.name == identifier, models.User.role == models.RoleEnum.technician)
            .first()
        )
    if not user:
        raise HTTPException(status_code=400, detail="Assigned technician not found")
    return user


def _find_ticket(db: Session, ticket_id: str) -> models.Ticket | None:
    ticket = db.query(models.Ticket).filter(models.Ticket.ticket_number == ticket_id).first()
    if not ticket and ticket_id.isdigit():
        ticket = db.query(models.Ticket).filter(models.Ticket.id == int(ticket_id)).first()
    return ticket


def _get_ticket_or_404(db: Session, ticket_id: str) -> models.Ticket:
    ticket = _find_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


def _format_technician_scheduled_time(ticket: models.Ticket) -> str:
    return ticket.scheduled_time.isoformat() if ticket.scheduled_time else "Не назначено"


def _today_almaty() -> date:
    return datetime.now(scheduler.TZ_ALMATY).date()


def _notify_assigned_technician(db: Session, ticket: models.Ticket) -> None:
    if not ticket.assigned_to:
        return

    try:
        tenant = ticket.tenant
        building = tenant.building if tenant else None
        message = notifier.generate_technician_assignment_message(
            technician_name=ticket.assignee.name if ticket.assignee else "",
            ticket_number=ticket.ticket_number,
            tenant_name=tenant.name if tenant else "N/A",
            building_name=building.name if building else "N/A",
            apartment=tenant.apartment if tenant else "N/A",
            description=ticket.description or "",
            category=ticket.category or "General",
            urgency=ticket.urgency or "LOW",
            scheduled_time=_format_technician_scheduled_time(ticket),
            building_address=building.address if building else "",
            building_house_number=building.house_number if building else "",
            building_floor=building.floor if building else "",
            building_block=building.block if building else "",
        )
        notifier.notify_technician(db, ticket.assigned_to, message)
    except Exception:
        logger.exception("Failed to notify technician for ticket %s", ticket.ticket_number)


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
        tenantName=ticket.tenant.name if ticket.tenant else None,
        tenantId=ticket.tenant.id if ticket.tenant else None,
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
    current_user: models.User = Depends(get_dispatcher_user)
):
    tickets = (
        db.query(models.Ticket)
        .options(
            joinedload(models.Ticket.tenant).joinedload(models.Tenant.building),
            joinedload(models.Ticket.assignee),
        )
        .order_by(
            case((models.Ticket.scheduled_time.is_(None), 1), else_=0),
            models.Ticket.scheduled_time.asc(),
            models.Ticket.created_at.desc(),
        )
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [format_ticket_list(t) for t in tickets]

@router.post("/export")
def export_tickets(
    body: schemas.TicketExportRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_dispatcher_user),
):
    tickets = (
        db.query(models.Ticket)
        .filter(models.Ticket.ticket_number.in_(body.ticket_ids))
        .all()
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Tickets"

    headers = [
        "Ticket #", "Tenant", "Building", "Apartment", "Category",
        "Urgency", "Status", "Description", "Created", "Assigned To",
        "Scheduled", "Notes",
    ]
    ws.append(headers)
    bold = Font(bold=True)
    for cell in ws[1]:
        cell.font = bold

    for t in tickets:
        tenant_name = t.tenant.name if t.tenant else ""
        building = t.tenant.building.name if t.tenant and t.tenant.building else ""
        apartment = t.tenant.apartment if t.tenant else ""
        assigned = t.assignee.name if t.assignee else ""
        scheduled = t.scheduled_time.isoformat() if t.scheduled_time else ""
        created = t.created_at.isoformat() if t.created_at else ""
        notes_text = "\n".join(
            f"[{n.author.name if n.author else 'Unknown'}] {n.text}"
            for n in t.notes
        )
        ws.append([
            t.ticket_number, tenant_name, building, apartment,
            t.category or "", t.urgency or "", t.status.value if t.status else "",
            t.description or "", created, assigned, scheduled, notes_text,
        ])

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=tickets_export.xlsx"},
    )


@router.get("/{ticket_id}", response_model=schemas.TicketDispatcherDetailResponse)
def read_ticket(
    ticket_id: str, 
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_dispatcher_user)
):
    ticket = _get_ticket_or_404(db, ticket_id)
    return format_ticket_detail(ticket)

@router.post("", response_model=schemas.TicketDispatcherDetailResponse)
def create_ticket(
    ticket: schemas.TicketCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_dispatcher_user),
):
    import uuid
    data = ticket.model_dump()
    assigned_to = data.pop("assigned_to", None)
    scheduled_time = data.get("scheduled_time")
    if scheduled_time and not assigned_to:
        raise HTTPException(status_code=400, detail="Assign a technician before scheduling")

    if assigned_to:
        assignee = _find_technician(db, assigned_to)
        data["assigned_to"] = assignee.id
        if data.get("status") == models.TicketStatusEnum.new:
            data["status"] = models.TicketStatusEnum.assigned
        if scheduled_time:
            if not scheduler.verify_technician_slot_available(
                db,
                assignee.id,
                scheduled_time.isoformat(),
            ):
                raise HTTPException(status_code=400, detail="Selected slot is no longer available")
            data["scheduled_time"] = scheduled_time.replace(tzinfo=None)

    db_ticket = models.Ticket(**data, ticket_number=f"TKT-{str(uuid.uuid4())[:8].upper()}")
    db.add(db_ticket)
    db.commit()
    db.refresh(db_ticket)
    if db_ticket.assigned_to:
        _notify_assigned_technician(db, db_ticket)
    return format_ticket_detail(db_ticket)


@router.get("/{ticket_id}/available-slots", response_model=List[schemas.TicketAvailableSlotResponse])
def get_ticket_available_slots(
    ticket_id: str,
    target_date: Optional[date] = Query(None, alias="date", description="Date to search in YYYY-MM-DD format"),
    date_from: Optional[date] = Query(None, description="Start date for a range search (YYYY-MM-DD)"),
    days: int = Query(14, ge=1, le=30, description="Number of days to search when date_from is used"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_dispatcher_user),
):
    ticket = _get_ticket_or_404(db, ticket_id)
    if not ticket.assigned_to:
        raise HTTPException(status_code=400, detail="Assign a technician before scheduling")

    if target_date:
        return scheduler.find_slots_for_technician_on_date(
            db,
            ticket.assigned_to,
            target_date,
            exclude_ticket_id=ticket.id,
        )

    return scheduler.find_slots_for_technician_in_range(
        db,
        ticket.assigned_to,
        date_from or _today_almaty(),
        days=days,
        exclude_ticket_id=ticket.id,
    )

@router.put("/{ticket_id}", response_model=schemas.TicketDispatcherDetailResponse)
def update_ticket(
    ticket_id: str, 
    ticket_update: dict,  
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_dispatcher_user)
):
    ticket = _get_ticket_or_404(db, ticket_id)
    previous_scheduled_time = ticket.scheduled_time

    if "status" in ticket_update:
        status_str = ticket_update["status"].lower()
        try:
            ticket.status = models.TicketStatusEnum(status_str)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {ticket_update['status']}")
    
    assignee_value = ticket_update.get("assignedTo", ticket_update.get("assigned_to"))
    if "assignedTo" in ticket_update or "assigned_to" in ticket_update:
        if assignee_value:
            ticket.assigned_to = _find_technician(db, assignee_value).id
            if ticket.status == models.TicketStatusEnum.new:
                ticket.status = models.TicketStatusEnum.assigned
        else:
            ticket.assigned_to = None
            
    scheduled_value = ticket_update.get("scheduledDate", ticket_update.get("scheduled_time"))
    if "scheduledDate" in ticket_update or "scheduled_time" in ticket_update:
        parsed_scheduled = _parse_datetime(scheduled_value, "scheduled_time")
        if parsed_scheduled is None:
            ticket.scheduled_time = None
        else:
            if not ticket.assigned_to:
                raise HTTPException(status_code=400, detail="Assign a technician before scheduling")
            if not scheduler.verify_technician_slot_available(
                db,
                ticket.assigned_to,
                parsed_scheduled.isoformat(),
                exclude_ticket_id=ticket.id,
            ):
                raise HTTPException(status_code=400, detail="Selected slot is no longer available")
            ticket.scheduled_time = parsed_scheduled.replace(tzinfo=None)

    if "urgency" in ticket_update:
        allowed = {"low", "medium", "high", "emergency"}
        urgency_str = ticket_update["urgency"].upper()
        if urgency_str.lower() not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid urgency: {ticket_update['urgency']}. Allowed: {', '.join(allowed)}",
            )
        ticket.urgency = urgency_str

    if "description" in ticket_update:
        ticket.description = ticket_update["description"] or ""

    if "category" in ticket_update:
        ticket.category = ticket_update["category"]

    if "availability_time" in ticket_update:
        ticket.availability_time = ticket_update["availability_time"]

    db.commit()
    db.refresh(ticket)
    if (
        ("scheduledDate" in ticket_update or "scheduled_time" in ticket_update)
        and ticket.scheduled_time is not None
        and ticket.scheduled_time != previous_scheduled_time
    ):
        _notify_assigned_technician(db, ticket)
    return format_ticket_detail(ticket)


@router.delete("/{ticket_id}")
def delete_ticket(
    ticket_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_dispatcher_user),
):
    ticket = _get_ticket_or_404(db, ticket_id)
    db.delete(ticket)
    db.commit()
    return {"detail": "Ticket deleted"}


@router.post("/{ticket_id}/notes", response_model=schemas.TicketNoteSchema)
def add_note(
    ticket_id: str,
    note: schemas.NoteCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_dispatcher_user)
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
    current_user: models.User = Depends(get_dispatcher_user),
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
