import os
import json
import logging
from pathlib import Path
from openai import OpenAI

from ..schemas import AgentResponse

logger = logging.getLogger("uvicorn.error")

_client = None
_system_prompt = None

AGENT_INSTRUCTION = """Based on the full conversation history above, do TWO things:

1. **Reply** to the tenant naturally. Match the language they use (Russian → Russian, English → English, etc.).
   - If they greet you, greet back warmly and ask how you can help.
   - If they describe an issue or ask a question, acknowledge it.
   - Keep replies concise: 1-2 sentences.

2. **Classify** their intent — but ONLY if you have enough information.
   - If the conversation is just greetings or vague ("I have a problem"), set classified=false.
   - If the tenant has clearly stated their intent, set classified=true with scenario/confidence/subtype.

Return ONLY valid JSON with this exact structure:
{
  "reply": "your reply to the tenant",
  "classified": true or false,
  "scenario": "service" | "faq" | "billing" | "announcement" | "unknown" | null,
  "confidence": 0.0 to 1.0 or null,
  "subtype": "brief_label_of_specific_issue" or null,
  "requires_human": true or false
}

Scenario rules:
- "service" = maintenance/repair request (AC broken, leak, electrical issue, etc.)
- "faq" = question about policies, hours, rules, procedures
- "billing" = payment, utility bill, receipt, due date inquiry
- "announcement" = inbound information/notice (water shutdown, maintenance schedule, etc.)
- "unknown" = cannot determine intent even after conversation
- Set requires_human=true if the message is aggressive, confusing, or involves legal/contract issues
- When classified=false, set scenario, confidence, and subtype to null
- When classified=true, confidence below 0.65 means you are unsure"""


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        token = os.getenv("OPENAI_TOKEN", "")
        _client = OpenAI(api_key=token)
    return _client


def _get_system_prompt() -> str:
    global _system_prompt
    if _system_prompt is None:
        rules_path = Path(__file__).resolve().parent.parent.parent / "rules.txt"
        if rules_path.exists():
            _system_prompt = rules_path.read_text(encoding="utf-8")
        else:
            _system_prompt = "You are an AI maintenance helpdesk assistant for a property management company."
    return _system_prompt


def process_message(message_history: list[dict[str, str]]) -> AgentResponse:
    """Process tenant message: generate reply and optionally classify intent.

    Args:
        message_history: List of {"role": "tenant"|"ai", "content": "..."} dicts.

    Returns:
        AgentResponse with reply, classification status, and optional scenario.
    """
    try:
        client = _get_client()
        system_prompt = _get_system_prompt()

        messages = [{"role": "system", "content": system_prompt}]

        for msg in message_history[-20:]:
            role = "user" if msg["role"] == "tenant" else "assistant"
            messages.append({"role": role, "content": msg["content"]})

        messages.append({"role": "user", "content": AGENT_INSTRUCTION})

        response = client.chat.completions.create(
            model="gpt-5.4",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.1,
            max_completion_tokens=500,
        )

        raw = response.choices[0].message.content
        data = json.loads(raw)

        return AgentResponse(
            reply=data.get("reply", ""),
            classified=bool(data.get("classified", False)),
            scenario=data.get("scenario"),
            confidence=float(data["confidence"]) if data.get("confidence") is not None else None,
            subtype=data.get("subtype"),
            requires_human=bool(data.get("requires_human", False)),
        )

    except Exception as e:
        logger.error(f"Agent processing failed: {e}")
        return AgentResponse(
            reply="Произошла ошибка. Ваш запрос передан диспетчеру.",
            classified=False,
            scenario=None,
            confidence=None,
            subtype=None,
            requires_human=True,
        )


def call_llm(
    message_history: list[dict[str, str]],
    instruction: str,
    extra_context: str = "",
) -> dict:
    """Generic LLM call with a custom instruction.

    Used by orchestrator state handlers for detail extraction, slot
    presentation, selection parsing, and confirmation.

    Args:
        message_history: Conversation messages [{"role": "tenant"|"ai", "content": "..."}].
        instruction: Task-specific instruction appended as the last user message.
        extra_context: Optional extra context (e.g. available slots JSON) prepended to instruction.

    Returns:
        Parsed JSON dict from the LLM response, or a fallback error dict.
    """
    try:
        client = _get_client()
        system_prompt = _get_system_prompt()

        messages = [{"role": "system", "content": system_prompt}]
        for msg in message_history[-20:]:
            role = "user" if msg["role"] == "tenant" else "assistant"
            messages.append({"role": role, "content": msg["content"]})

        full_instruction = f"{extra_context}\n\n{instruction}" if extra_context else instruction
        messages.append({"role": "user", "content": full_instruction})

        response = client.chat.completions.create(
            model="gpt-5.4",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.1,
            max_completion_tokens=500,
        )

        raw = response.choices[0].message.content
        return json.loads(raw)

    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return {"reply": "Произошла ошибка. Ваш запрос передан диспетчеру.", "error": True}
