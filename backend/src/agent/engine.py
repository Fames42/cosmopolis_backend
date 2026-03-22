"""AgentEngine — agentic loop via Responses API with tool execution."""

import json
import re
import logging
from datetime import datetime, timezone, date as date_type, timedelta
from pathlib import Path

from .context import ConversationContext
from .llm import TOOL_DEFINITIONS
from .protocols import ConversationStore, SchedulingService, NotificationService, LLMClient
from .types import (
    ConversationState,
    ConversationSnapshot,
    ConversationStateUpdate,
    Scenario,
    TenantInfo,
    HistoryMessage,
    AgentResult,
    SlotInfo,
)

logger = logging.getLogger("uvicorn.error")

# UTC+5 (Almaty/Astana timezone)
TZ_ALMATY = timezone(timedelta(hours=5))

ESCALATION_COOLDOWN_HOURS = 12

# Tool name → state mapping for dashboard consistency
TOOL_STATE_MAP = {
    "update_service_details": ConversationState.service_collecting_details,
    "search_available_slots": ConversationState.service_scheduling,
    "select_time_slot": ConversationState.service_ready_for_ticket,
    "create_ticket": ConversationState.ticket_created,
    "escalate_to_human": ConversationState.escalated_to_human,
    "close_conversation": ConversationState.closed,
    "lookup_my_tickets": ConversationState.managing_ticket,
    "reschedule_ticket": ConversationState.managing_ticket,
    "add_ticket_comment": ConversationState.managing_ticket,
    "cancel_ticket": ConversationState.managing_ticket,
}

MAX_TOOL_ROUNDS = 8


# ── Helpers ──────────────────────────────────────────────────────────────────

def _digits_only(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def _normalize_kz(digits: str) -> str:
    if len(digits) == 11 and digits.startswith("8"):
        return "7" + digits[1:]
    return digits


def _build_state_preamble(ctx: ConversationContext) -> str:
    """Build a state summary for the model's context."""
    now_almaty = datetime.now(TZ_ALMATY)
    data = ctx.context_data
    lines = [
        f"TODAY: {now_almaty.strftime('%Y-%m-%d')} ({now_almaty.strftime('%A')})",
        f"CURRENT_TIME: {now_almaty.strftime('%H:%M')}",
        "",
        f"TENANT: {ctx.tenant.name}",
        f"BUILDING: {ctx.tenant.building_name}, apt. {ctx.tenant.apartment}",
    ]

    # Include any previously collected details
    collected = {}
    for key in ("category", "urgency", "problem", "description", "location",
                "danger_now", "preferred_date", "preferred_time", "photo_received"):
        val = data.get(key)
        if val is not None:
            collected[key] = val
    if collected:
        lines.append("")
        lines.append("PREVIOUSLY COLLECTED:")
        for k, v in collected.items():
            lines.append(f"  {k}: {v}")

    ticket_number = data.get("ticket_number")
    if ticket_number:
        lines.append("")
        lines.append(f"TICKET CREATED FOR CURRENT ISSUE: {ticket_number} — do not create another ticket for this same issue.")

    return "\n".join(lines)


# ── AgentEngine ──────────────────────────────────────────────────────────────

class AgentEngine:
    """Agentic engine — delegates flow control to the LLM via tool calls."""

    def __init__(
        self,
        store: ConversationStore,
        scheduler: SchedulingService,
        notifier: NotificationService,
        llm: LLMClient,
        prompts_dir: Path | None = None,
    ):
        self.store = store
        self.scheduler = scheduler
        self.notifier = notifier
        self.llm = llm
        self._agent_prompt: str = ""
        if prompts_dir:
            agent_prompt_path = prompts_dir / "agent.txt"
            if agent_prompt_path.exists():
                self._agent_prompt = agent_prompt_path.read_text(encoding="utf-8")

    def save_incoming_message(
        self, phone: str, content: str, image_base64: str | None = None,
    ) -> tuple[TenantInfo | None, ConversationSnapshot | None, str]:
        """Save an incoming tenant message to DB without processing it."""
        tenant = self.store.find_tenant_by_phone(phone)
        if not tenant:
            return None, None, ""

        chat_id = f"{_digits_only(phone)}@c.us"
        snapshot = self.store.get_or_create_conversation(tenant.id, chat_id)
        self.store.save_message(snapshot.id, "tenant", content, image_base64=image_base64)

        return tenant, snapshot, chat_id

    def process_conversation(
        self, ctx: ConversationContext,
    ) -> tuple[str, str, AgentResult | None]:
        """Agentic loop: LLM decides tool calls, we execute them, repeat until text reply."""

        # ── Check if agent support is enabled ────────────────────────────
        if not ctx.tenant.agent_enabled:
            logger.info("Agent support disabled for tenant %s", ctx.tenant.id)
            return "", "agent_disabled", None

        # ── Check escalation state ──────────────────────────────────────
        if ctx.state == ConversationState.escalated_to_human:
            if ctx.escalated_at:
                escalated_at = ctx.escalated_at
                if escalated_at.tzinfo is None:
                    escalated_at = escalated_at.replace(tzinfo=timezone.utc)
                elapsed = datetime.now(timezone.utc) - escalated_at
                if elapsed < timedelta(hours=ESCALATION_COOLDOWN_HOURS):
                    logger.info(
                        "Conversation %s is escalated, AI paused (%.1fh remaining)",
                        ctx.conversation_id,
                        ESCALATION_COOLDOWN_HOURS - elapsed.total_seconds() / 3600,
                    )
                    return "", ctx.state.value, None

            # Cooldown expired — auto-reset
            logger.info("Escalation cooldown expired for conversation %s, resetting", ctx.conversation_id)
            ctx.state = ConversationState.new_conversation
            ctx.escalated_at = None
            ctx.context_data = {}  # clears response_id too — fresh start
            ctx.scenario = None
            ctx.reopened_at = datetime.now(timezone.utc)
            self.store.update_conversation(ctx.conversation_id, ctx.to_state_update())

        # ── Get last tenant message ──────────────────────────────────────
        history = self.store.get_message_history(ctx.conversation_id, since=ctx.reopened_at)

        last_tenant_content = ""
        for msg in reversed(history):
            if msg.role == "tenant":
                last_tenant_content = msg.content
                break

        if not last_tenant_content:
            return "", ctx.state.value, None

        # ── Build instructions with state preamble ───────────────────────
        state_preamble = _build_state_preamble(ctx)
        instructions = f"{self._agent_prompt}\n\n--- CURRENT STATE ---\n{state_preamble}"

        # ── Agentic loop ─────────────────────────────────────────────────
        previous_response_id = ctx.context_data.get("response_id")
        tools_called: list[str] = []
        tools_log: list[dict] = []  # {"name": ..., "args": ..., "result": ...}
        reply_text = None

        # Initial call
        reply_text, tool_calls, response_id = self.llm.run(
            user_message=last_tenant_content,
            instructions=instructions,
            tools=TOOL_DEFINITIONS,
            previous_response_id=previous_response_id,
        )

        if response_id:
            ctx.update_context({"response_id": response_id})

        # Tool execution loop
        rounds = 0
        while tool_calls and rounds < MAX_TOOL_ROUNDS:
            rounds += 1
            tool_outputs = []

            for call in tool_calls:
                name = call["name"]
                call_id = call["call_id"]
                try:
                    args = json.loads(call["arguments"]) if call["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}

                logger.info("Tool call: %s(%s)", name, json.dumps(args, ensure_ascii=False))
                tools_called.append(name)

                result = self._execute_tool(name, args, ctx, history)
                tools_log.append({"name": name, "args": args, "result": result})
                tool_outputs.append({
                    "call_id": call_id,
                    "output": json.dumps(result, ensure_ascii=False),
                })

            # Submit tool outputs and get next response
            reply_text, tool_calls, response_id = self.llm.submit_tool_outputs(
                tool_outputs=tool_outputs,
                previous_response_id=response_id or previous_response_id or "",
                instructions=instructions,
                tools=TOOL_DEFINITIONS,
            )

            if response_id:
                ctx.update_context({"response_id": response_id})

        # ── Fallback if no reply ─────────────────────────────────────────
        if not reply_text:
            logger.warning("No reply text from LLM after %d rounds", rounds)
            reply_text = (
                "Произошла ошибка. Ваш запрос передан диспетчеру.\n"
                "An error occurred. Your request has been forwarded to the dispatcher."
            )

        # ── Update state based on tools called ───────────────────────────
        last_state = None
        for tool_name in tools_called:
            mapped = TOOL_STATE_MAP.get(tool_name)
            if mapped:
                last_state = mapped

        if last_state:
            ctx.state = last_state
            if last_state == ConversationState.closed:
                ctx.status = "closed"
            if last_state == ConversationState.escalated_to_human:
                ctx.escalated_at = datetime.now(timezone.utc)

        # Detect scenario from tools
        ticket_mgmt_tools = {"lookup_my_tickets", "reschedule_ticket", "add_ticket_comment", "cancel_ticket"}
        if "update_service_details" in tools_called or "search_available_slots" in tools_called:
            ctx.scenario = Scenario.service.value
        elif ticket_mgmt_tools & set(tools_called):
            ctx.scenario = Scenario.service.value
        elif "escalate_to_human" in tools_called:
            ctx.scenario = ctx.scenario or Scenario.unknown.value

        # If conversation just started and no tools called, set gathering
        if ctx.state == ConversationState.new_conversation and not tools_called:
            ctx.state = ConversationState.gathering

        # ── Persist ──────────────────────────────────────────────────────
        self.store.update_conversation(ctx.conversation_id, ctx.to_state_update())
        self.store.save_message(ctx.conversation_id, "ai", reply_text)

        # ── Build result ─────────────────────────────────────────────────
        classified = bool(ctx.scenario and ctx.scenario != "unknown")
        agent_result = AgentResult(
            reply=reply_text,
            classified=classified,
            scenario=ctx.scenario,
            confidence=0.9 if classified else None,
            subtype=ctx.context_data.get("category"),
            requires_human="escalate_to_human" in tools_called,
            tools_called=tools_log,
        )

        return reply_text, ctx.state.value, agent_result

    def _execute_tool(
        self,
        name: str,
        args: dict,
        ctx: ConversationContext,
        history: list[HistoryMessage],
    ) -> dict:
        """Dispatch a tool call to the appropriate backend service."""

        if name == "update_service_details":
            # If a ticket was already created and the agent is collecting details
            # for a NEW issue, clear stale ticket context so the LLM doesn't
            # think a ticket already exists for this request.
            if ctx.context_data.get("ticket_number"):
                for stale_key in ("ticket_number", "offered_slots", "slots_presented",
                                  "selected_slot_index"):
                    ctx.context_data.pop(stale_key, None)

            updates = {}
            if "category" in args:
                updates["category"] = args["category"]
                updates["service_category"] = args["category"]
            if "urgency" in args:
                updates["urgency"] = args["urgency"]
            if "problem" in args:
                updates["description"] = args["problem"]
                updates["problem"] = args["problem"]
            if "location" in args:
                updates["location"] = args["location"]
            if "danger_now" in args:
                updates["danger_now"] = args["danger_now"]
            if "preferred_date" in args:
                updates["preferred_date"] = args["preferred_date"]
            if "preferred_time" in args:
                updates["preferred_time"] = args["preferred_time"]
            if "photo_received" in args:
                updates["photo_received"] = args["photo_received"]
            ctx.update_context(updates)
            return {"status": "ok", "saved_fields": list(updates.keys())}

        elif name == "search_available_slots":
            category = ctx.context_data.get("category", "other")
            urgency = ctx.context_data.get("urgency", "medium")
            preferred_date = args.get("preferred_date")
            preferred_time = args.get("preferred_time")

            # When rescheduling, exclude the ticket being rescheduled so its
            # current time slot doesn't block the search.
            exclude_tid = ctx.context_data.get("reschedule_ticket_id")

            slots: list[SlotInfo] = []
            if preferred_date:
                try:
                    target = date_type.fromisoformat(preferred_date)
                except ValueError:
                    logger.warning("Invalid preferred_date: %s", preferred_date)
                    return {"status": "error", "message": "Invalid date format. Use YYYY-MM-DD."}

                # If both date and time given, check that exact time first
                if preferred_time:
                    try:
                        parts = preferred_time.split(":")
                        req_hour, req_minute = int(parts[0]), int(parts[1])
                    except (ValueError, IndexError):
                        return {"status": "error", "message": "Invalid time format. Use HH:MM."}
                    slots = self.scheduler.find_slot_for_time(
                        category, target, req_hour, req_minute, exclude_tid)
                    if slots:
                        logger.info("Exact slot at %s %s found for %s", preferred_date, preferred_time, category)
                    else:
                        # Exact time unavailable — fall back to all slots for that date
                        logger.info("Exact time %s unavailable, showing alternatives", preferred_time)
                        slots = self.scheduler.find_slots_for_date(category, target, exclude_tid)
                else:
                    slots = self.scheduler.find_slots_for_date(category, target, exclude_tid)
                    logger.info("Slots for %s (%s): %d found", preferred_date, category, len(slots))
            else:
                slots = self.scheduler.find_available_slots(category, urgency, num_slots=3)
                if not slots:
                    slots = self.scheduler.find_available_slots(category, "low", num_slots=3)

            if slots:
                slot_dicts = [
                    {"technician_id": s.technician_id, "technician_name": s.technician_name,
                     "start": s.start, "end": s.end}
                    for s in slots
                ]
                ctx.update_context({"offered_slots": slot_dicts, "slots_presented": True})
                return {
                    "status": "ok",
                    "slots": [
                        {"index": i, "start": s.start, "end": s.end, "technician_name": s.technician_name}
                        for i, s in enumerate(slots)
                    ],
                }
            else:
                if urgency == "emergency":
                    return {"status": "no_slots", "message": "No slots available. Consider escalating for emergency."}
                return {"status": "no_slots", "message": "No slots available on this date. Ask the tenant for another date."}

        elif name == "select_time_slot":
            slot_index = args.get("slot_index", 0)
            offered = ctx.context_data.get("offered_slots", [])

            if not (0 <= slot_index < len(offered)):
                return {"status": "error", "message": f"Invalid slot index {slot_index}. Available: 0-{len(offered)-1}"}

            slot = offered[slot_index]
            if self.scheduler.verify_slot_available(slot["technician_id"], slot["start"]):
                ctx.update_context({"selected_slot_index": slot_index})
                return {
                    "status": "ok",
                    "selected": {
                        "index": slot_index,
                        "start": slot["start"],
                        "end": slot["end"],
                        "technician_name": slot["technician_name"],
                    },
                }
            else:
                # Slot taken — refresh
                category = ctx.context_data.get("category", "other")
                urgency = ctx.context_data.get("urgency", "medium")
                new_slots = self.scheduler.find_available_slots(category, urgency, num_slots=3)
                if new_slots:
                    new_dicts = [
                        {"technician_id": s.technician_id, "technician_name": s.technician_name,
                         "start": s.start, "end": s.end}
                        for s in new_slots
                    ]
                    ctx.update_context({"offered_slots": new_dicts, "slots_presented": True})
                    return {
                        "status": "slot_taken",
                        "message": "That slot was just taken. Here are updated options.",
                        "slots": [
                            {"index": i, "start": s.start, "end": s.end, "technician_name": s.technician_name}
                            for i, s in enumerate(new_slots)
                        ],
                    }
                return {"status": "slot_taken", "message": "That slot is no longer available and no alternatives found."}

        elif name == "create_ticket":
            if ctx.context_data.get("ticket_number"):
                return {"status": "already_exists", "ticket_number": ctx.context_data["ticket_number"]}

            ticket = self.scheduler.create_ticket(ctx.tenant.id, ctx.context_data, ctx.conversation_id)
            ctx.update_context({"ticket_number": ticket.ticket_number})

            # Notify technician if assigned
            if ticket.assigned_to:
                offered = ctx.context_data.get("offered_slots", [])
                selected_idx = ctx.context_data.get("selected_slot_index", 0)
                slot = offered[selected_idx] if selected_idx < len(offered) else {}
                self.notifier.notify_technician_assigned(
                    technician_name=slot.get("technician_name", "—"),
                    ticket_number=ticket.ticket_number,
                    tenant=ctx.tenant,
                    description=ticket.description or "",
                    category=ticket.category or "",
                    urgency=ticket.urgency or "",
                    scheduled_time=slot.get("start", ""),
                )

            return {"status": "ok", "ticket_number": ticket.ticket_number}

        elif name == "escalate_to_human":
            reason = args.get("reason", "")
            last_msg = ""
            for msg in reversed(history):
                if msg.role == "tenant":
                    last_msg = msg.content
                    break

            self.notifier.escalate(ctx.tenant, ctx.phone, last_msg, history)
            ctx.escalated_at = datetime.now(timezone.utc)
            return {"status": "ok", "message": "Escalated to dispatcher."}

        elif name == "lookup_my_tickets":
            summaries = self.scheduler.lookup_tenant_tickets(ctx.tenant.id)
            if not summaries:
                return {"status": "ok", "tickets": [], "message": "No active tickets found."}
            # Store ticket_number → ticket_id mapping for reschedule exclusion
            ticket_id_map = {s.ticket_number: s.ticket_id for s in summaries if s.ticket_id}
            ctx.update_context({"ticket_id_map": ticket_id_map})
            # If only one active ticket, pre-set it as the reschedule target
            if len(summaries) == 1:
                ctx.update_context({
                    "reschedule_ticket_id": summaries[0].ticket_id,
                    "reschedule_ticket_number": summaries[0].ticket_number,
                })
            return {
                "status": "ok",
                "tickets": [
                    {
                        "ticket_number": s.ticket_number,
                        "category": s.category,
                        "status": s.status,
                        "description": s.description,
                        "scheduled_time": s.scheduled_time,
                        "technician": s.assigned_to_name,
                    }
                    for s in summaries
                ],
            }

        elif name == "reschedule_ticket":
            ticket_number = args.get("ticket_number", "")
            slot_index = args.get("slot_index", 0)
            offered = ctx.context_data.get("offered_slots", [])

            if not ticket_number:
                return {"status": "error", "message": "ticket_number is required."}
            if not (0 <= slot_index < len(offered)):
                return {"status": "error", "message": f"Invalid slot index {slot_index}. Search for slots first."}

            slot = offered[slot_index]
            if not self.scheduler.verify_slot_available(slot["technician_id"], slot["start"]):
                return {"status": "error", "message": "That slot is no longer available. Search for new slots."}

            result = self.scheduler.reschedule_ticket(
                ticket_number, ctx.tenant.id, slot["technician_id"], slot["start"],
            )
            if result:
                return {
                    "status": "ok",
                    "ticket_number": result.ticket_number,
                    "new_time": slot["start"],
                    "technician": slot.get("technician_name", ""),
                }
            return {"status": "error", "message": "Ticket not found or already completed."}

        elif name == "add_ticket_comment":
            ticket_number = args.get("ticket_number", "")
            comment = args.get("comment", "")
            if not ticket_number or not comment:
                return {"status": "error", "message": "ticket_number and comment are required."}
            success = self.scheduler.add_ticket_comment(ticket_number, ctx.tenant.id, comment)
            if success:
                return {"status": "ok", "message": "Comment added to ticket."}
            return {"status": "error", "message": "Ticket not found or access denied."}

        elif name == "cancel_ticket":
            ticket_number = args.get("ticket_number", "")
            if not ticket_number:
                return {"status": "error", "message": "ticket_number is required."}
            success = self.scheduler.cancel_ticket(ticket_number, ctx.tenant.id)
            if success:
                return {"status": "ok", "message": f"Ticket {ticket_number} has been cancelled."}
            return {"status": "error", "message": "Ticket not found, already completed, or access denied."}

        elif name == "close_conversation":
            ctx.state = ConversationState.closed
            ctx.status = "closed"
            return {"status": "ok", "message": "Conversation closed."}

        else:
            logger.warning("Unknown tool: %s", name)
            return {"status": "error", "message": f"Unknown tool: {name}"}
