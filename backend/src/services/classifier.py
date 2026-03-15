"""Two-step LLM pipeline: Router (classification) + Writer (tenant-facing reply)."""

import os
import json
import logging
from pathlib import Path
from openai import OpenAI

logger = logging.getLogger("uvicorn.error")

_client = None
_prompts: dict[str, str] = {}


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        token = os.getenv("OPENAI_TOKEN", "")
        _client = OpenAI(api_key=token)
    return _client


def _load_prompt(name: str) -> str:
    """Load and cache a prompt file from the prompts/ directory."""
    if name not in _prompts:
        prompt_path = Path(__file__).resolve().parent.parent.parent / "prompts" / f"{name}.txt"
        if prompt_path.exists():
            _prompts[name] = prompt_path.read_text(encoding="utf-8")
        else:
            logger.error("Prompt file not found: %s", prompt_path)
            _prompts[name] = ""
    return _prompts[name]


def step1_route(
    message_history: list[dict[str, str]],
    state_context: str,
) -> dict:
    """Step 1: Router / state extractor.

    Analyzes tenant message + conversation state, returns structured JSON
    for backend processing. No tenant-facing prose.

    Args:
        message_history: List of {"role": "tenant"|"ai", "content": "..."}.
        state_context: Serialized conversation state (current step, collected fields, offered slots).

    Returns:
        Parsed JSON dict matching the RouterResponse schema.
    """
    try:
        client = _get_client()
        router_prompt = _load_prompt("router")

        messages = [{"role": "system", "content": router_prompt}]
        for msg in message_history[-20:]:
            role = "user" if msg["role"] == "tenant" else "assistant"
            messages.append({"role": role, "content": msg["content"]})

        messages.append({"role": "user", "content": state_context})

        response = client.chat.completions.create(
            model="gpt-5.4",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.05,
            max_completion_tokens=500,
        )

        raw = response.choices[0].message.content
        return json.loads(raw)

    except Exception as e:
        logger.error("Router (step1) failed: %s", e)
        return {
            "language": "ru",
            "intent": "unknown",
            "requires_human": True,
            "cancel_requested": False,
            "service_category": None,
            "urgency": None,
            "collected_fields": {},
            "missing_fields": [],
            "next_step": "escalate",
            "ready_for_confirmation": False,
            "ready_for_ticket": False,
            "notes_for_backend": {},
        }


def step2_write(
    router_json: dict,
    last_tenant_message: str,
    backend_results: dict | None = None,
) -> str:
    """Step 2: Tenant-facing response writer.

    Takes the Router JSON + tenant message + optional backend results,
    returns a friendly WhatsApp message as plain text.

    Args:
        router_json: Output from step1_route().
        last_tenant_message: The tenant's most recent message text.
        backend_results: Optional dict with slots, ticket_id, faq_answer, etc.

    Returns:
        Plain text reply string for the tenant.
    """
    try:
        client = _get_client()
        writer_prompt = _load_prompt("writer")

        # Build the input for the writer
        parts = [
            "WORKFLOW JSON:",
            json.dumps(router_json, ensure_ascii=False, indent=2),
            "",
            "TENANT MESSAGE:",
            last_tenant_message,
        ]
        if backend_results:
            parts.extend(["", "BACKEND RESULTS:", json.dumps(backend_results, ensure_ascii=False, indent=2)])

        user_content = "\n".join(parts)

        messages = [
            {"role": "system", "content": writer_prompt},
            {"role": "user", "content": user_content},
        ]

        response = client.chat.completions.create(
            model="gpt-5.4",
            messages=messages,
            temperature=0.3,
            max_completion_tokens=300,
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error("Writer (step2) failed: %s", e)
        return "Произошла ошибка. Ваш запрос передан диспетчеру.\nAn error occurred. Your request has been forwarded to the dispatcher."
