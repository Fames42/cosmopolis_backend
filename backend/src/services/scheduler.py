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

SLOT_DURATION_MINUTES = 60


def _parse_time(t: str) -> time:
    """Parse 'HH:MM' string to time object."""
    h, m = t.split(":")
    return time(int(h), int(m))


def _to_minutes(t: time) -> int:
    """Convert time to minutes from midnight."""
    return t.hour * 60 + t.minute


def _generate_time_blocks(start: time, end: time) -> list[tuple[int, int]]:
    """Return (hour, minute) tuples at 30-min intervals that fit a full slot before end.

    Example (1h slots): start=09:00, end=18:00
    → (9,0), (9,30), (10,0), ..., (17,0)
    """
    blocks = []
    start_min = _to_minutes(start)
    end_min = _to_minutes(end)
    cur = start_min
    while cur + SLOT_DURATION_MINUTES <= end_min:
        blocks.append((cur // 60, cur % 60))
        cur += 30
    return blocks


def _get_occupied_ranges(
    db: Session, tech_id: str, check_date: date, exclude_ticket_id: int | None = None,
) -> list[tuple[int, int]]:
    """Return list of (start_min, end_min) occupied ranges for a technician on a date."""
    day_start = datetime(check_date.year, check_date.month, check_date.day, tzinfo=TZ_ALMATY)
    day_end = day_start + timedelta(days=1)
    q = (
        db.query(models.Ticket)
        .filter(
            models.Ticket.assigned_to == tech_id,
            models.Ticket.scheduled_time >= day_start,
            models.Ticket.scheduled_time < day_end,
            models.Ticket.status.notin_([
                models.TicketStatusEnum.done,
                models.TicketStatusEnum.cancelled,
            ]),
        )
    )
    if exclude_ticket_id is not None:
        q = q.filter(models.Ticket.id != exclude_ticket_id)
    existing = q.all()
    ranges = []
    for ticket in existing:
        if ticket.scheduled_time:
            s = ticket.scheduled_time.hour * 60 + ticket.scheduled_time.minute
            ranges.append((s, s + SLOT_DURATION_MINUTES))
    return ranges


def _overlaps(start_min: int, end_min: int, occupied: list[tuple[int, int]]) -> bool:
    """Check if [start_min, end_min) overlaps with any occupied range."""
    for occ_start, occ_end in occupied:
        if start_min < occ_end and end_min > occ_start:
            return True
    return False


def _find_matching_techs(db: Session, category: str):
    """Find technicians whose specialties include the given category."""
    category_lower = category.lower()
    all_techs = (
        db.query(models.User)
        .filter(models.User.role == models.RoleEnum.technician)
        .all()
    )
    return [
        tech for tech in all_techs
        if category_lower in [s.lower() for s in (tech.specialties or [])]
    ]


def _load_schedule_map(
    db: Session, tech_ids: list[str],
) -> dict[tuple[str, int], tuple[time, time]]:
    """Load technician schedules indexed by (tech_id, day_of_week)."""
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
    return schedule_map


def _make_slot_dict(tech, check_date: date, hour: int, minute: int) -> dict:
    """Build a slot dict for a technician at a given date/time."""
    slot_start = datetime(
        check_date.year, check_date.month, check_date.day,
        hour, minute, tzinfo=TZ_ALMATY,
    )
    slot_end = slot_start + timedelta(minutes=SLOT_DURATION_MINUTES)
    return {
        "technician_id": tech.id,
        "technician_name": tech.name,
        "start": slot_start.isoformat(),
        "end": slot_end.isoformat(),
    }


def find_slot_for_time(
    db: Session,
    category: str,
    target_date: date,
    hour: int,
    minute: int,
    exclude_ticket_id: int | None = None,
) -> list[dict]:
    """Check if any technician is free at target_date hour:minute for 1h.

    Returns a single-element list [slot_dict] if available, [] if not.
    """
    matching_techs = _find_matching_techs(db, category)
    if not matching_techs:
        return []

    tech_ids = [t.id for t in matching_techs]
    schedule_map = _load_schedule_map(db, tech_ids)
    weekday = target_date.weekday()

    req_start = hour * 60 + minute
    req_end = req_start + SLOT_DURATION_MINUTES

    now = datetime.now(TZ_ALMATY)
    if target_date == now.date() and req_start <= now.hour * 60 + now.minute:
        return []

    for tech in matching_techs:
        key = (tech.id, weekday)
        if key not in schedule_map:
            continue

        work_start, work_end = schedule_map[key]
        if req_start < _to_minutes(work_start) or req_end > _to_minutes(work_end):
            continue

        occupied = _get_occupied_ranges(db, tech.id, target_date, exclude_ticket_id)
        if not _overlaps(req_start, req_end, occupied):
            return [_make_slot_dict(tech, target_date, hour, minute)]

    return []


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
    now_min = now.hour * 60 + now.minute
    window_days = URGENCY_WINDOW.get(urgency.lower(), 3)

    matching_techs = _find_matching_techs(db, category)
    if not matching_techs:
        logger.warning("No technicians found with specialty '%s'", category)
        return []

    tech_ids = [t.id for t in matching_techs]
    schedule_map = _load_schedule_map(db, tech_ids)

    slots: list[dict] = []

    for day_offset in range(window_days):
        check_date = today + timedelta(days=day_offset)
        weekday = check_date.weekday()

        for tech in matching_techs:
            key = (tech.id, weekday)
            if key not in schedule_map:
                continue

            work_start, work_end = schedule_map[key]
            all_blocks = _generate_time_blocks(work_start, work_end)
            occupied = _get_occupied_ranges(db, tech.id, check_date)

            for hour, minute in all_blocks:
                slot_start_min = hour * 60 + minute
                slot_end_min = slot_start_min + SLOT_DURATION_MINUTES

                if _overlaps(slot_start_min, slot_end_min, occupied):
                    continue
                if check_date == today and slot_start_min <= now_min:
                    continue

                slots.append(_make_slot_dict(tech, check_date, hour, minute))

                if len(slots) >= num_slots * 3:
                    break

            if len(slots) >= num_slots * 3:
                break
        if len(slots) >= num_slots * 3:
            break

    # Pick representative spread of slots (earliest, middle, latest if enough)
    if len(slots) <= num_slots:
        return slots

    step = max(1, len(slots) // num_slots)
    picked = [slots[i * step] for i in range(num_slots) if i * step < len(slots)]
    return picked[:num_slots]


def find_slots_for_date(
    db: Session,
    category: str,
    target_date: date,
    exclude_ticket_id: int | None = None,
) -> list[dict]:
    """Find ALL available 1-hour slots for a specific date."""
    now = datetime.now(TZ_ALMATY)
    today = now.date()
    now_min = now.hour * 60 + now.minute

    matching_techs = _find_matching_techs(db, category)
    if not matching_techs:
        logger.warning("No technicians found with specialty '%s'", category)
        return []

    tech_ids = [t.id for t in matching_techs]
    schedule_map = _load_schedule_map(db, tech_ids)
    weekday = target_date.weekday()
    slots: list[dict] = []

    for tech in matching_techs:
        key = (tech.id, weekday)
        if key not in schedule_map:
            continue

        work_start, work_end = schedule_map[key]
        all_blocks = _generate_time_blocks(work_start, work_end)
        occupied = _get_occupied_ranges(db, tech.id, target_date, exclude_ticket_id)

        for hour, minute in all_blocks:
            slot_start_min = hour * 60 + minute
            slot_end_min = slot_start_min + SLOT_DURATION_MINUTES

            if _overlaps(slot_start_min, slot_end_min, occupied):
                continue
            if target_date == today and slot_start_min <= now_min:
                continue

            slots.append(_make_slot_dict(tech, target_date, hour, minute))

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
    start_naive = start_dt.replace(tzinfo=None)
    check_date = start_naive.date()
    req_start = start_naive.hour * 60 + start_naive.minute
    req_end = req_start + SLOT_DURATION_MINUTES
    occupied = _get_occupied_ranges(db, technician_id, check_date)
    return not _overlaps(req_start, req_end, occupied)
