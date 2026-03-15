"""Technician scheduling: find available time slots and auto-create tickets."""

import logging
import uuid
from datetime import datetime, timedelta, timezone, date, time
from sqlalchemy.orm import Session

from .. import models

logger = logging.getLogger("uvicorn.error")

# UTC+5 (Almaty/Astana timezone)
TZ_ALMATY = timezone(timedelta(hours=5))

# How many days ahead to search, keyed by urgency
URGENCY_WINDOW = {
    "emergency": 1,
    "high": 2,
    "medium": 3,
    "low": 5,
}

SLOT_DURATION_HOURS = 2


def _parse_time(t: str) -> time:
    """Parse 'HH:MM' string to time object."""
    h, m = t.split(":")
    return time(int(h), int(m))


def _generate_hour_blocks(start: time, end: time) -> list[int]:
    """Return list of start-hours for SLOT_DURATION_HOURS blocks between start and end.

    Example (2h slots): start=09:00, end=18:00 → [9, 11, 13, 15]
    Only includes start hours where the full slot fits before end.
    """
    hours = []
    h = start.hour
    while h + SLOT_DURATION_HOURS <= end.hour:
        hours.append(h)
        h += SLOT_DURATION_HOURS
    return hours


def find_available_slots(
    db: Session,
    category: str,
    urgency: str,
    num_slots: int = 3,
) -> list[dict]:
    """Find available 1-hour slots for a service category.

    Returns up to `num_slots` entries like:
        [{"technician_id": "...", "technician_name": "...",
          "start": "2026-03-14T10:00", "end": "2026-03-14T11:00"}, ...]
    """
    now = datetime.now(TZ_ALMATY)
    today = now.date()
    window_days = URGENCY_WINDOW.get(urgency.lower(), 3)
    category_lower = category.lower()

    # 1. Find technicians with matching specialty
    all_techs = (
        db.query(models.User)
        .filter(models.User.role == models.RoleEnum.technician)
        .all()
    )
    matching_techs = []
    for tech in all_techs:
        specs = tech.specialties or []
        if category_lower in [s.lower() for s in specs]:
            matching_techs.append(tech)

    if not matching_techs:
        logger.warning("No technicians found with specialty '%s'", category)
        return []

    # 2. Load schedules for matching technicians
    tech_ids = [t.id for t in matching_techs]
    schedules = (
        db.query(models.TechnicianSchedule)
        .filter(models.TechnicianSchedule.technician_id.in_(tech_ids))
        .all()
    )
    # Index: (tech_id, day_of_week) → (start_time, end_time)
    schedule_map: dict[tuple[str, int], tuple[time, time]] = {}
    for s in schedules:
        schedule_map[(s.technician_id, s.day_of_week)] = (
            _parse_time(s.start_time),
            _parse_time(s.end_time),
        )

    # 3. For each day in window, find free slots
    slots: list[dict] = []

    for day_offset in range(window_days):
        check_date = today + timedelta(days=day_offset)
        weekday = check_date.weekday()  # 0=Monday

        for tech in matching_techs:
            key = (tech.id, weekday)
            if key not in schedule_map:
                continue  # Tech doesn't work this day

            work_start, work_end = schedule_map[key]
            all_hours = _generate_hour_blocks(work_start, work_end)

            # Get existing appointments for this tech on this day
            day_start = datetime(check_date.year, check_date.month, check_date.day, tzinfo=TZ_ALMATY)
            day_end = day_start + timedelta(days=1)
            existing = (
                db.query(models.Ticket)
                .filter(
                    models.Ticket.assigned_to == tech.id,
                    models.Ticket.scheduled_time >= day_start,
                    models.Ticket.scheduled_time < day_end,
                    models.Ticket.status.notin_([
                        models.TicketStatusEnum.done,
                        models.TicketStatusEnum.cancelled,
                    ]),
                )
                .all()
            )
            occupied_hours = set()
            for ticket in existing:
                if ticket.scheduled_time:
                    # Each ticket occupies SLOT_DURATION_HOURS hours
                    for h in range(SLOT_DURATION_HOURS):
                        occupied_hours.add(ticket.scheduled_time.hour + h)

            # Remove occupied hours and past hours (if today)
            for hour in all_hours:
                # Check that the full slot doesn't overlap with occupied hours
                if any(hour + h in occupied_hours for h in range(SLOT_DURATION_HOURS)):
                    continue
                # Skip past hours if checking today
                if check_date == today and hour <= now.hour:
                    continue

                slot_start = datetime(
                    check_date.year, check_date.month, check_date.day,
                    hour, 0, tzinfo=TZ_ALMATY,
                )
                slot_end = slot_start + timedelta(hours=SLOT_DURATION_HOURS)

                slots.append({
                    "technician_id": tech.id,
                    "technician_name": tech.name,
                    "start": slot_start.isoformat(),
                    "end": slot_end.isoformat(),
                })

                if len(slots) >= num_slots * 3:
                    # Enough candidates to pick from
                    break

            if len(slots) >= num_slots * 3:
                break
        if len(slots) >= num_slots * 3:
            break

    # 4. Pick representative spread of slots (earliest, middle, latest if enough)
    if len(slots) <= num_slots:
        return slots

    # Spread: pick first, middle, and last-ish to give variety
    step = max(1, len(slots) // num_slots)
    picked = [slots[i * step] for i in range(num_slots) if i * step < len(slots)]
    return picked[:num_slots]


def find_slots_for_date(
    db: Session,
    category: str,
    target_date: date,
) -> list[dict]:
    """Find ALL available 1-hour slots for a specific date."""
    now = datetime.now(TZ_ALMATY)
    today = now.date()
    category_lower = category.lower()

    all_techs = (
        db.query(models.User)
        .filter(models.User.role == models.RoleEnum.technician)
        .all()
    )
    matching_techs = [
        tech for tech in all_techs
        if category_lower in [s.lower() for s in (tech.specialties or [])]
    ]

    if not matching_techs:
        logger.warning("No technicians found with specialty '%s'", category)
        return []

    tech_ids = [t.id for t in matching_techs]
    schedules = (
        db.query(models.TechnicianSchedule)
        .filter(models.TechnicianSchedule.technician_id.in_(tech_ids))
        .all()
    )
    schedule_map: dict[tuple[str, int], tuple[time, time]] = {}
    for s in schedules:
        schedule_map[(s.technician_id, s.day_of_week)] = (
            _parse_time(s.start_time),
            _parse_time(s.end_time),
        )

    weekday = target_date.weekday()
    slots: list[dict] = []

    for tech in matching_techs:
        key = (tech.id, weekday)
        if key not in schedule_map:
            continue

        work_start, work_end = schedule_map[key]
        all_hours = _generate_hour_blocks(work_start, work_end)

        day_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=TZ_ALMATY)
        day_end = day_start + timedelta(days=1)
        existing = (
            db.query(models.Ticket)
            .filter(
                models.Ticket.assigned_to == tech.id,
                models.Ticket.scheduled_time >= day_start,
                models.Ticket.scheduled_time < day_end,
                models.Ticket.status.notin_([
                    models.TicketStatusEnum.done,
                    models.TicketStatusEnum.cancelled,
                ]),
            )
            .all()
        )
        occupied_hours = set()
        for t in existing:
            if t.scheduled_time:
                for h in range(SLOT_DURATION_HOURS):
                    occupied_hours.add(t.scheduled_time.hour + h)

        for hour in all_hours:
            if any(hour + h in occupied_hours for h in range(SLOT_DURATION_HOURS)):
                continue
            if target_date == today and hour <= now.hour:
                continue

            slot_start = datetime(
                target_date.year, target_date.month, target_date.day,
                hour, 0, tzinfo=TZ_ALMATY,
            )
            slot_end = slot_start + timedelta(hours=SLOT_DURATION_HOURS)

            slots.append({
                "technician_id": tech.id,
                "technician_name": tech.name,
                "start": slot_start.isoformat(),
                "end": slot_end.isoformat(),
            })

    return slots


def create_ticket_from_context(
    db: Session,
    tenant_id: int,
    context_data: dict,
    conversation_id: int,
) -> models.Ticket:
    """Create a Ticket from the conversation context_data.

    Expected context_data keys:
        category, urgency, description, offered_slots, selected_slot_index

    Photos are collected from the Messages table (last 3 hours) rather than
    context_data, avoiding race conditions when multiple images arrive
    concurrently across different workers.
    """
    selected_index = context_data.get("selected_slot_index", 0)
    offered_slots = context_data.get("offered_slots", [])
    slot = offered_slots[selected_index] if selected_index < len(offered_slots) else None

    ticket_number = f"TKT-{str(uuid.uuid4())[:8].upper()}"
    scheduled_time = None
    assigned_to = None

    if slot:
        # Strip timezone so the local Almaty time is stored as-is in the DB
        # (the DateTime column is timezone-naive)
        scheduled_time = datetime.fromisoformat(slot["start"]).replace(tzinfo=None)
        assigned_to = slot["technician_id"]

    # Collect photos from conversation messages (last 3 hours).
    # Images are stored reliably via INSERT in _save_message — no race conditions.
    cutoff = datetime.now(TZ_ALMATY) - timedelta(hours=3)
    photo_messages = (
        db.query(models.Message)
        .filter(
            models.Message.conversation_id == conversation_id,
            models.Message.sender == models.MessageSenderEnum.tenant,
            models.Message.message_type.in_([
                models.MessageTypeEnum.image,
                models.MessageTypeEnum.mixed,
            ]),
            models.Message.media_url.isnot(None),
            models.Message.created_at >= cutoff,
        )
        .order_by(models.Message.created_at.asc())
        .all()
    )
    photo_urls = [m.media_url for m in photo_messages]

    ticket = models.Ticket(
        ticket_number=ticket_number,
        tenant_id=tenant_id,
        category=context_data.get("category", "other"),
        urgency=context_data.get("urgency", "medium").upper(),
        description=context_data.get("description", ""),
        photo_urls=photo_urls,
        availability_time=slot["start"] if slot else "",
        assigned_to=assigned_to,
        status=models.TicketStatusEnum.scheduled,
        scheduled_time=scheduled_time,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    logger.info("Auto-created ticket %s for tenant %s", ticket_number, tenant_id)
    return ticket


def verify_slot_available(db: Session, technician_id: str, start_iso: str) -> bool:
    """Check that a slot hasn't been taken since it was offered."""
    start_dt = datetime.fromisoformat(start_iso)
    conflict = (
        db.query(models.Ticket)
        .filter(
            models.Ticket.assigned_to == technician_id,
            models.Ticket.scheduled_time == start_dt,
            models.Ticket.status.notin_([
                models.TicketStatusEnum.done,
                models.TicketStatusEnum.cancelled,
            ]),
        )
        .first()
    )
    return conflict is None
