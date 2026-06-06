"""Scheduled ticket reminder processing."""

import asyncio
import logging
from datetime import datetime, time, timedelta

from sqlalchemy.orm import Session, joinedload

from .. import models
from ..database import SessionLocal
from . import notifier, scheduler

logger = logging.getLogger("uvicorn.error")

REMINDER_SCAN_INTERVAL_SECONDS = 60
REMINDER_CUTOFF_HOUR = 14
MORNING_REMINDER_TIME = time(9, 0)
EVENING_REMINDER_TIME = time(21, 0)

_reminder_task: asyncio.Task | None = None


def _as_almaty(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=scheduler.TZ_ALMATY)
    return value.astimezone(scheduler.TZ_ALMATY)


def _scheduled_key(value: datetime) -> str:
    return _as_almaty(value).replace(tzinfo=None).isoformat()


def compute_ticket_reminder_due_at(scheduled_time: datetime) -> datetime:
    """Return the Almaty-local datetime when a reminder should be sent."""
    visit = _as_almaty(scheduled_time)
    if visit.hour < REMINDER_CUTOFF_HOUR:
        reminder_date = visit.date() - timedelta(days=1)
        reminder_time = EVENING_REMINDER_TIME
    else:
        reminder_date = visit.date()
        reminder_time = MORNING_REMINDER_TIME
    return datetime.combine(reminder_date, reminder_time, tzinfo=scheduler.TZ_ALMATY)


def _reminder_already_sent(ticket: models.Ticket) -> bool:
    if not ticket.scheduled_time:
        return True
    state = dict(ticket.reminder_state or {})
    return state.get("sent_for") == _scheduled_key(ticket.scheduled_time)


def _mark_reminder_sent(ticket: models.Ticket, now: datetime) -> None:
    ticket.reminder_state = {
        "sent_for": _scheduled_key(ticket.scheduled_time),
        "sent_at": _as_almaty(now).isoformat(),
    }


def _building_name(ticket: models.Ticket) -> str:
    if ticket.tenant and ticket.tenant.building:
        return ticket.tenant.building.name
    return "N/A"


def _apartment(ticket: models.Ticket) -> str:
    return ticket.tenant.apartment if ticket.tenant else "N/A"


def _send_ticket_reminder(db: Session, ticket: models.Ticket) -> None:
    tenant = ticket.tenant
    building = tenant.building if tenant else None
    building_name = building.name if building else "N/A"
    apartment = tenant.apartment if tenant else "N/A"

    if tenant and tenant.phone:
        notifier.notify_tenant_ticket_reminder(
            tenant_phone=tenant.phone,
            ticket_number=ticket.ticket_number,
            tenant_name=tenant.name,
            building_name=building_name,
            apartment=apartment,
            scheduled_time=ticket.scheduled_time,
            description=ticket.description or "",
        )

    notifier.notify_technician_lifecycle(
        db=db,
        technician_id=ticket.assigned_to,
        action="reminder",
        ticket_number=ticket.ticket_number,
        tenant_name=tenant.name if tenant else "N/A",
        building_name=building_name,
        apartment=apartment,
        scheduled_time=ticket.scheduled_time,
        reason="Плановое напоминание о визите.",
        description=ticket.description or "",
        category=ticket.category or "General",
        urgency=ticket.urgency or "LOW",
        building_address=building.address if building else "",
        building_house_number=building.house_number if building else "",
        building_floor=building.floor if building else "",
        building_block=building.block if building else "",
    )


def process_due_ticket_reminders(db: Session, now: datetime | None = None) -> int:
    """Send due ticket reminders and return the number of tickets processed."""
    now_almaty = _as_almaty(now or datetime.now(scheduler.TZ_ALMATY))
    tickets = (
        db.query(models.Ticket)
        .options(
            joinedload(models.Ticket.tenant).joinedload(models.Tenant.building),
            joinedload(models.Ticket.assignee),
        )
        .filter(
            models.Ticket.assigned_to.isnot(None),
            models.Ticket.scheduled_time.isnot(None),
            models.Ticket.status.notin_([
                models.TicketStatusEnum.cancelled,
                models.TicketStatusEnum.done,
            ]),
        )
        .all()
    )

    sent_count = 0
    for ticket in tickets:
        visit_at = _as_almaty(ticket.scheduled_time)
        if visit_at <= now_almaty:
            continue
        due_at = compute_ticket_reminder_due_at(ticket.scheduled_time)
        if due_at > now_almaty:
            continue
        if _reminder_already_sent(ticket):
            continue

        _send_ticket_reminder(db, ticket)
        _mark_reminder_sent(ticket, now_almaty)
        sent_count += 1

    if sent_count:
        db.commit()
    return sent_count


async def run_ticket_reminder_loop(interval_seconds: int = REMINDER_SCAN_INTERVAL_SECONDS) -> None:
    while True:
        try:
            db = SessionLocal()
            try:
                sent_count = process_due_ticket_reminders(db)
                if sent_count:
                    logger.info("Sent %d ticket reminder(s)", sent_count)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ticket reminder loop failed")
        await asyncio.sleep(interval_seconds)


def start_ticket_reminder_loop() -> None:
    global _reminder_task
    if _reminder_task and not _reminder_task.done():
        return
    _reminder_task = asyncio.create_task(run_ticket_reminder_loop())


async def stop_ticket_reminder_loop() -> None:
    global _reminder_task
    if not _reminder_task:
        return
    _reminder_task.cancel()
    try:
        await _reminder_task
    except asyncio.CancelledError:
        pass
    _reminder_task = None
