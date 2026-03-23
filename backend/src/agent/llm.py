"""LLM integration — Responses API agentic loop + utility generation."""

import json
import logging
from pathlib import Path

from openai import OpenAI

from .types import HistoryMessage

logger = logging.getLogger("uvicorn.error")

# ── Tool definitions for the Responses API ───────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "name": "update_service_details",
        "description": "Save or update collected information about the tenant's service request. Call this whenever you learn new details.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["plumbing", "electrical", "heating", "appliance", "structural", "other"],
                    "description": "Service category",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "emergency"],
                    "description": "Urgency level",
                },
                "problem": {"type": "string", "description": "Description of the problem"},
                "location": {"type": "string", "description": "Location within the apartment (room/area)"},
                "danger_now": {"type": "boolean", "description": "Whether there is immediate danger"},
                "preferred_date": {"type": "string", "description": "Preferred date for technician visit (ISO format YYYY-MM-DD)"},
                "preferred_time": {"type": "string", "description": "Preferred time for technician visit (HH:MM format, e.g. '09:30', '14:00')"},
                "photo_received": {"type": "boolean", "description": "Whether a photo was received"},
            },
            "required": [],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "search_available_slots",
        "description": "Search for available technician time slots. IMPORTANT: For emergencies (urgency=emergency), you MUST call this tool — it auto-escalates to the dispatcher and returns the on-call technician's contact info. All parameters are optional. Call with no arguments to get nearest slots, with just preferred_date to get all slots for that day, or with both for an exact time check. Do NOT ask the tenant repeatedly for a precise time — search proactively and present options. When rescheduling, pass ticket_number so its current slot is excluded from the search.",
        "parameters": {
            "type": "object",
            "properties": {
                "preferred_date": {
                    "type": "string",
                    "description": "Target date in ISO format (YYYY-MM-DD). If not provided, searches for nearest available slots.",
                },
                "preferred_time": {
                    "type": "string",
                    "description": "Preferred time in HH:MM format (e.g., '09:30', '14:00'). If provided with preferred_date, checks availability at that exact time first.",
                },
                "ticket_number": {
                    "type": "string",
                    "description": "Ticket number being rescheduled (e.g., 'TKT-ABCD1234'). Pass this when searching slots for a reschedule so the ticket's current slot is not blocked.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "select_time_slot",
        "description": "Select a specific time slot from the previously offered options after the tenant chooses one.",
        "parameters": {
            "type": "object",
            "properties": {
                "slot_index": {
                    "type": "integer",
                    "description": "Zero-based index of the selected slot from the offered list.",
                },
            },
            "required": ["slot_index"],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "create_ticket",
        "description": "Create a maintenance ticket after the tenant confirms all details. Only call after confirmation.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "escalate_to_human",
        "description": "Transfer the conversation to a human dispatcher. Use when the tenant requests a human, the issue is too complex, or the tenant is upset. For emergencies, prefer calling search_available_slots instead — it auto-escalates AND provides the technician's contact info.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Reason for escalation",
                },
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "close_conversation",
        "description": "Close or cancel the conversation when the tenant is done or cancels.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "lookup_my_tickets",
        "description": "Look up the tenant's active (non-done, non-cancelled) tickets. Call when the tenant asks about an existing ticket, wants to reschedule, cancel, or add information.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "reschedule_ticket",
        "description": "Reschedule an existing ticket to a new time slot. Call after the tenant confirms the new time. Requires a ticket_number and a slot_index from previously searched slots.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_number": {"type": "string", "description": "The ticket number to reschedule (e.g. TKT-ABCD1234)"},
                "slot_index": {"type": "integer", "description": "Zero-based index of the new slot from the offered list"},
            },
            "required": ["ticket_number", "slot_index"],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "add_ticket_comment",
        "description": "Add a comment/note to an existing ticket from the tenant. Use when the tenant wants to provide additional information about their issue.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_number": {"type": "string", "description": "The ticket number"},
                "comment": {"type": "string", "description": "The comment text to add"},
            },
            "required": ["ticket_number", "comment"],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "cancel_ticket",
        "description": "Cancel an existing ticket. Only call after the tenant explicitly confirms they want to cancel.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_number": {"type": "string", "description": "The ticket number to cancel"},
            },
            "required": ["ticket_number"],
            "additionalProperties": False,
        },
        "strict": False,
    },
]


def _parse_response_output(output: list) -> tuple[str | None, list[dict]]:
    """Parse Responses API output into (reply_text, tool_calls)."""
    reply_text = None
    tool_calls = []

    for item in output:
        if item.type == "message":
            for content in item.content:
                if content.type == "output_text":
                    reply_text = content.text
        elif item.type == "function_call":
            tool_calls.append({
                "call_id": item.call_id,
                "name": item.name,
                "arguments": item.arguments,
            })

    return reply_text, tool_calls


class OpenAILLMClient:
    """Implements LLMClient protocol using OpenAI Responses API."""

    def __init__(self, api_key: str, prompts_dir: Path, model: str = "gpt-5.4"):
        self._client = OpenAI(api_key=api_key)
        self._prompts_dir = prompts_dir
        self._model = model
        self._prompt_cache: dict[str, str] = {}

    def _load_prompt(self, name: str) -> str:
        if name not in self._prompt_cache:
            path = self._prompts_dir / f"{name}.txt"
            if path.exists():
                self._prompt_cache[name] = path.read_text(encoding="utf-8")
            else:
                logger.error("Prompt file not found: %s", path)
                self._prompt_cache[name] = ""
        return self._prompt_cache[name]

    def run(
        self,
        user_message: str,
        instructions: str,
        tools: list[dict],
        previous_response_id: str | None = None,
    ) -> tuple[str | None, list[dict], str | None]:
        """Call Responses API with user message, tools, and optional continuity."""
        try:
            kwargs = {
                "model": self._model,
                "instructions": instructions,
                "input": [{"role": "user", "content": user_message}],
                "tools": tools,
                "store": True,
                "temperature": 0.3,
                "max_output_tokens": 500,
            }
            if previous_response_id:
                kwargs["previous_response_id"] = previous_response_id

            response = self._client.responses.create(**kwargs)

            reply_text, tool_calls = _parse_response_output(response.output)
            return reply_text, tool_calls, response.id

        except Exception as e:
            logger.error("Responses API call failed: %s", e)
            return None, [], None

    def submit_tool_outputs(
        self,
        tool_outputs: list[dict],
        previous_response_id: str,
        instructions: str,
        tools: list[dict],
    ) -> tuple[str | None, list[dict], str | None]:
        """Submit tool results and get next response."""
        try:
            input_items = []
            for output in tool_outputs:
                input_items.append({
                    "type": "function_call_output",
                    "call_id": output["call_id"],
                    "output": output["output"],
                })

            response = self._client.responses.create(
                model=self._model,
                instructions=instructions,
                input=input_items,
                tools=tools,
                previous_response_id=previous_response_id,
                store=True,
                temperature=0.3,
                max_output_tokens=500,
            )

            reply_text, tool_calls = _parse_response_output(response.output)
            return reply_text, tool_calls, response.id

        except Exception as e:
            logger.error("Tool output submission failed: %s", e)
            return None, [], None

    def generate_message(self, prompt_name: str, user_content: str, fallback: str) -> str:
        """Generic LLM message generation (for escalation, assignment, etc.)."""
        try:
            prompt = self._load_prompt(prompt_name)
            if not prompt:
                return fallback

            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.3,
                max_completion_tokens=300,
            )
            return response.choices[0].message.content.strip()

        except Exception:
            logger.exception("Failed to generate message via LLM (%s), using fallback", prompt_name)
            return fallback
