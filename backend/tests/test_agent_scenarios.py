"""
AI Agent scenario tests — 10 LLM-driven tenant personas message the agent,
conversations are logged, and a third LLM analyzes quality.

Run against a live Docker backend:
    1. docker compose -f backend/docker-compose.yml up --build -d
    2. python backend/tests/test_agent_scenarios.py --verbose
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from openai import OpenAI

# ── Config ───────────────────────────────────────────────────────────────────

DEFAULT_BASE = "http://localhost:8000/api"
MAX_TURNS = 15
REQUEST_TIMEOUT = 90  # seconds — 5s buffer + LLM latency
TENANT_MODEL = "gpt-5.4"
ANALYZER_MODEL = "gpt-5.4"
TEST_PHONE_PREFIX = "770011"  # phones: 770011XXXXXX (timestamp-based suffix for isolation)

# Module-level base URL — set by main() before any calls
BASE = DEFAULT_BASE

# ── Scenario definitions ────────────────────────────────────────────────────

SCENARIOS = [
    {
        "id": "01_plumbing_happy_path",
        "name": "Happy Path — Plumbing",
        "tenant_name": "Иван Петров",
        "apartment": "101",
        "language": "Russian",
        "expected_states": ["ticket_created", "closed"],
        "persona": (
            "You are Иван Петров, a tenant in apartment 101. "
            "You have a leaking pipe under your kitchen sink. Water is dripping slowly onto the floor. "
            "You are calm and cooperative. Answer all the agent's questions honestly. "
            "When offered time slots, pick the first one. When asked to confirm, say yes."
        ),
    },
    {
        "id": "02_emergency_electrical",
        "name": "Emergency — Electrical",
        "tenant_name": "Мария Соколова",
        "apartment": "205",
        "language": "Russian",
        "expected_states": ["ticket_created", "escalated_to_human", "closed"],
        "persona": (
            "You are Мария Соколова, a tenant in apartment 205. "
            "You see sparks coming from a wall outlet in your bedroom. You smell something burning. "
            "You are very worried and want urgent help. If asked about danger, say YES — there is immediate danger. "
            "Be cooperative but convey urgency in every message."
        ),
    },
    {
        "id": "03_heating_date_preference",
        "name": "Heating — Date Preference",
        "tenant_name": "Дмитрий Казаков",
        "apartment": "312",
        "language": "Russian",
        "expected_states": ["ticket_created", "closed"],
        "persona": (
            "You are Дмитрий Казаков, a tenant in apartment 312. "
            "Your radiator in the living room stopped working. It's cold in the apartment. "
            "You want a technician to come specifically on Thursday. If Thursday is not available, "
            "accept the nearest available day. When offered slots, pick the first one. Confirm when asked."
        ),
    },
    {
        "id": "04_appliance_repair",
        "name": "Appliance Repair — Dishwasher",
        "tenant_name": "Аня Федорова",
        "apartment": "118",
        "language": "Russian",
        "expected_states": ["ticket_created", "closed"],
        "persona": (
            "You are Аня Федорова, a tenant in apartment 118. "
            "Your dishwasher won't start — it makes a clicking sound but nothing happens. "
            "It's not urgent, just inconvenient. Go through the flow cooperatively. "
            "Accept the first available slot and confirm."
        ),
    },
    {
        "id": "05_faq_question",
        "name": "FAQ — Office Hours",
        "tenant_name": "Сергей Волков",
        "apartment": "403",
        "language": "Russian",
        "expected_states": ["gathering", "classified_faq", "escalated_to_human", "closed"],
        "persona": (
            "You are Сергей Волков, a tenant in apartment 403. "
            "You want to know the management office hours and ask about the guest parking policy. "
            "You do NOT have a maintenance issue. If the agent tries to ask about a repair, "
            "clarify that you only have questions. After getting answers (or being told to contact the office), say thanks and goodbye."
        ),
    },
    {
        "id": "06_billing_inquiry",
        "name": "Billing Inquiry — Rent Increase",
        "tenant_name": "Елена Морозова",
        "apartment": "210",
        "language": "Russian",
        "expected_states": ["escalated_to_human", "closed"],
        "persona": (
            "You are Елена Морозова, a tenant in apartment 210. "
            "You want to know why your rent increased this month. You want payment details and an explanation. "
            "Be polite but insistent. You are not reporting a maintenance issue."
        ),
    },
    {
        "id": "07_cancellation_mid_flow",
        "name": "Cancellation Mid-Flow",
        "tenant_name": "Алексей Дубров",
        "apartment": "107",
        "language": "Russian",
        "expected_states": ["closed"],
        "persona": (
            "You are Алексей Дубров, a tenant in apartment 107. "
            "Start by reporting a broken door handle in your bathroom. "
            "After the agent asks 2-3 questions, change your mind and say: "
            "'Ладно, не надо, я сам починил. Отмена.' Cancel the request."
        ),
    },
    {
        "id": "08_escalation_request",
        "name": "Escalation — Demand Human",
        "tenant_name": "Наталья Иванова",
        "apartment": "501",
        "language": "Russian",
        "expected_states": ["escalated_to_human"],
        "persona": (
            "You are Наталья Иванова, a tenant in apartment 501. "
            "You had a terrible experience with a previous repair — they damaged your wall. "
            "You are frustrated and angry. You immediately demand to speak with a human operator/manager. "
            "Do NOT engage with the bot's questions. Keep insisting on a human."
        ),
    },
    {
        "id": "09_multi_language",
        "name": "Multi-Language — English",
        "tenant_name": "James Wilson",
        "apartment": "415",
        "language": "English",
        "expected_states": ["ticket_created", "closed"],
        "persona": (
            "You are James Wilson, an English-speaking expat tenant in apartment 415. "
            "Your bathroom faucet is leaking — it drips constantly. "
            "Write ONLY in English. Be friendly and cooperative. "
            "Accept the first available slot, confirm when asked."
        ),
    },
    {
        "id": "10_vague_complaint",
        "name": "Vague Complaint — Unclear Issue",
        "tenant_name": "Гульнара Ахметова",
        "apartment": "309",
        "language": "Russian",
        "expected_states": ["service_collecting_details", "ticket_created", "escalated_to_human", "closed"],
        "persona": (
            "You are Гульнара Ахметова, a tenant in apartment 309. "
            "Something is wrong in your apartment but you're not exactly sure what. "
            "First message: vaguely say 'something is wrong, there's a strange noise at night.' "
            "When the agent asks questions, gradually reveal it might be the pipes making noise in the walls. "
            "Eventually cooperate once you understand it's probably a plumbing issue. "
            "Accept any offered slot and confirm."
        ),
    },
    # ── Multi-phase scenarios: ticket management ──────────────────────
    {
        "id": "11_reschedule_ticket",
        "name": "Reschedule Ticket",
        "tenant_name": "Олег Краснов",
        "apartment": "220",
        "language": "Russian",
        "expected_states": ["closed"],
        "multi_phase": True,
        "phases": [
            {
                "persona": (
                    "You are Олег Краснов, a tenant in apartment 220. "
                    "Your kitchen faucet is dripping. It's not urgent but annoying. "
                    "Be cooperative, accept the first available slot, confirm when asked."
                ),
                "terminal_states": ["closed"],
            },
            {
                "persona": (
                    "You are Олег Краснов. You previously created a ticket for a dripping faucet "
                    "but now you need to reschedule it because you have a meeting at the original time. "
                    "Start by saying you need to change the appointment time. "
                    "When offered new slots, pick the second option if available, otherwise the first. "
                    "Confirm the new time when asked."
                ),
                "terminal_states": ["closed"],
            },
        ],
    },
    {
        "id": "12_cancel_ticket",
        "name": "Cancel Ticket",
        "tenant_name": "Вера Павлова",
        "apartment": "133",
        "language": "Russian",
        "expected_states": ["closed"],
        "multi_phase": True,
        "phases": [
            {
                "persona": (
                    "You are Вера Павлова, a tenant in apartment 133. "
                    "Your bathroom light stopped working. "
                    "Be cooperative, accept the first available slot, confirm when asked."
                ),
                "terminal_states": ["closed"],
            },
            {
                "persona": (
                    "You are Вера Павлова. You previously created a ticket for a broken bathroom light "
                    "but your husband already fixed it. You want to cancel the ticket. "
                    "Start by saying you want to cancel your maintenance request. "
                    "When asked to confirm cancellation, confirm."
                ),
                "terminal_states": ["closed"],
            },
        ],
    },
    {
        "id": "13_add_comment_to_ticket",
        "name": "Add Comment to Ticket",
        "tenant_name": "Рустам Бекенов",
        "apartment": "406",
        "language": "Russian",
        "expected_states": ["closed"],
        "multi_phase": True,
        "phases": [
            {
                "persona": (
                    "You are Рустам Бекенов, a tenant in apartment 406. "
                    "Your washing machine is not draining properly. "
                    "Be cooperative, accept the first available slot, confirm when asked."
                ),
                "terminal_states": ["closed"],
            },
            {
                "persona": (
                    "You are Рустам Бекенов. You previously created a ticket for a washing machine issue. "
                    "You want to add some information: the machine also started making a loud grinding noise. "
                    "Also mention that the entry code to your apartment building is 4521. "
                    "Start by saying you want to add info to your existing request."
                ),
                "terminal_states": ["closed"],
            },
        ],
    },
    {
        "id": "14_create_second_ticket",
        "name": "Create Second Ticket (New Issue)",
        "tenant_name": "Анна Сергеева",
        "apartment": "515",
        "language": "Russian",
        "expected_states": ["closed"],
        "multi_phase": True,
        "phases": [
            {
                "persona": (
                    "You are Анна Сергеева, a tenant in apartment 515. "
                    "Your kitchen oven is not heating up. "
                    "Be cooperative, accept the first available slot, confirm when asked."
                ),
                "terminal_states": ["closed"],
            },
            {
                "persona": (
                    "You are Анна Сергеева, a tenant in apartment 515. "
                    "You have a COMPLETELY NEW problem — your bathroom door handle is broken and won't close. "
                    "This is a new issue, not related to the previous oven ticket. "
                    "When the agent asks if you want to manage an existing request or report a new issue, "
                    "clearly say it's a new problem. Be cooperative, accept the first slot, confirm when asked."
                ),
                "terminal_states": ["closed"],
            },
        ],
    },
    {
        "id": "15_cancel_mid_reschedule",
        "name": "Cancel Mid-Reschedule",
        "tenant_name": "Кирилл Лебедев",
        "apartment": "302",
        "language": "Russian",
        "expected_states": ["closed"],
        "multi_phase": True,
        "phases": [
            {
                "persona": (
                    "You are Кирилл Лебедев, a tenant in apartment 302. "
                    "Your AC is not cooling. It's summer and uncomfortable. "
                    "Be cooperative, accept the first available slot, confirm when asked."
                ),
                "terminal_states": ["closed"],
            },
            {
                "persona": (
                    "You are Кирилл Лебедев. You previously created a ticket for a broken AC. "
                    "Start by saying you want to reschedule the appointment. "
                    "When the agent shows your ticket and asks for a new time, change your mind and say: "
                    "'Нет, не надо, оставьте как есть. Спасибо.' (No, leave it as is.)"
                ),
                "terminal_states": ["closed"],
            },
        ],
    },
]

PERSONA_SYSTEM_TEMPLATE = """\
You are simulating a tenant who is texting a WhatsApp assistant for their building's management company.

{persona}

RULES:
- Write short WhatsApp-style messages (1-3 sentences max)
- Write ONLY in {language}
- Do NOT mention tool names, states, API details, or anything technical
- When the agent asks you a question, answer naturally based on your situation
- When the agent offers numbered time slots, pick one by number or description
- When asked to confirm, respond naturally (yes/confirm)
- If you have nothing more to say, write a brief goodbye

Generate ONLY your next message as the tenant. Nothing else — no quotes, no labels.\
"""

ANALYZER_SYSTEM = """\
You are a QA analyst evaluating an AI WhatsApp agent for a property management system.

The agent handles maintenance requests, FAQ, billing, escalation, and cancellation scenarios.
It has these tools: update_service_details, search_available_slots, select_time_slot, \
create_ticket, escalate_to_human, close_conversation, lookup_my_tickets, \
reschedule_ticket, add_ticket_comment, cancel_ticket.

For each scenario, evaluate on a 1-10 scale:
1. NATURALNESS: Does the agent sound warm, human, and professional? Not robotic?
2. CORRECTNESS: Did the agent correctly classify the issue (category, urgency)?
3. TOOL USAGE: Did the agent use the right tools in the right order?
4. LANGUAGE: Did the agent respond in the tenant's language? Was grammar/style good?
5. PROTOCOL: Did the agent follow the correct workflow for this scenario type?

For each scenario, provide:
- Scores (5 criteria)
- Issues found (if any)
- Notable positives

At the end provide:
- Overall average score across all scenarios
- Top 3 issues across all scenarios
- Top 3 strengths
- Recommendations for improvement

Write the analysis in English.\
"""


# ── Helpers ──────────────────────────────────────────────────────────────────

def log(msg: str, verbose: bool = True):
    if verbose:
        print(msg)


def api_login(email: str, password: str) -> str:
    """Login and return JWT token."""
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ── Setup & Cleanup ─────────────────────────────────────────────────────────

def get_or_create_building(token: str) -> int:
    """Return a building_id, creating one if none exist."""
    headers = auth_headers(token)
    r = requests.get(f"{BASE}/agents/buildings", headers=headers)
    r.raise_for_status()
    buildings = r.json()
    if buildings:
        return buildings[0]["id"]

    r = requests.post(f"{BASE}/agents/buildings", headers=headers, json={
        "name": "Test Building",
        "address": "Test Street 1",
    })
    r.raise_for_status()
    return r.json()["id"]


def setup_tenants(token: str, building_id: int, scenarios: list[dict], phones: list[str], verbose: bool) -> dict[str, int]:
    """Create test tenants. Returns {phone: tenant_id}."""
    headers = auth_headers(token)
    tenant_ids = {}

    # Check existing tenants
    r = requests.get(f"{BASE}/agents/tenants", headers=headers)
    r.raise_for_status()
    existing = {t["phone"]: t["id"] for t in r.json()}

    for i, sc in enumerate(scenarios, 1):
        phone = phones[i - 1]
        if phone in existing:
            tenant_ids[phone] = existing[phone]
            log(f"  Tenant {phone} already exists (id={existing[phone]})", verbose)
            continue

        r = requests.post(f"{BASE}/agents/tenants", headers=headers, json={
            "name": sc["tenant_name"],
            "phone": phone,
            "apartment": sc["apartment"],
            "building_id": building_id,
            "agent_enabled": True,
        })
        if r.status_code == 400 and "already exists" in r.text:
            # Race condition or stale data — fetch again
            r2 = requests.get(f"{BASE}/agents/tenants", headers=headers)
            r2.raise_for_status()
            for t in r2.json():
                if t["phone"] == phone:
                    tenant_ids[phone] = t["id"]
                    break
            log(f"  Tenant {phone} already existed (re-fetched)", verbose)
            continue

        r.raise_for_status()
        tenant_ids[phone] = r.json()["id"]
        log(f"  Created tenant {sc['tenant_name']} ({phone})", verbose)

    return tenant_ids


def cleanup_tenants(token: str, tenant_ids: dict[str, int], verbose: bool):
    """Delete test tenants."""
    headers = auth_headers(token)
    for phone, tid in tenant_ids.items():
        try:
            requests.delete(f"{BASE}/agents/tenants/{tid}", headers=headers)
            log(f"  Deleted tenant {phone} (id={tid})", verbose)
        except Exception as e:
            log(f"  Failed to delete {phone}: {e}", verbose)


# ── Scenario Runner ─────────────────────────────────────────────────────────

def generate_tenant_message(client: OpenAI, messages: list[dict], model: str) -> str:
    """Use LLM to generate the next tenant message."""
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.7,
        max_completion_tokens=150,
    )
    return response.choices[0].message.content.strip()


def run_scenario(
    client: OpenAI,
    scenario: dict,
    phone: str,
    model: str,
    verbose: bool,
) -> list[dict]:
    """Run a single scenario. Returns list of turn entries."""
    persona_prompt = PERSONA_SYSTEM_TEMPLATE.format(
        persona=scenario["persona"],
        language=scenario["language"],
    )
    llm_messages = [{"role": "system", "content": persona_prompt}]

    # Generate initial tenant message
    tenant_msg = generate_tenant_message(client, llm_messages, model)
    log_entries = []

    for turn in range(1, MAX_TURNS + 1):
        log(f"  [Turn {turn}] Tenant: {tenant_msg[:80]}...", verbose)

        # Send to webhook
        try:
            r = requests.post(
                f"{BASE}/webhook/test",
                json={"phone": phone, "message": tenant_msg},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.Timeout:
            log(f"  [Turn {turn}] TIMEOUT after {REQUEST_TIMEOUT}s", verbose)
            log_entries.append({
                "turn": turn, "tenant": tenant_msg,
                "agent": "[TIMEOUT]", "state": "error", "agent_response": None,
            })
            break

        if r.status_code != 200:
            log(f"  [Turn {turn}] HTTP {r.status_code}: {r.text[:100]}", verbose)
            log_entries.append({
                "turn": turn, "tenant": tenant_msg,
                "agent": f"[HTTP {r.status_code}]", "state": "error", "agent_response": None,
            })
            break

        data = r.json()
        agent_reply = data.get("reply", "")
        state = data.get("state", "")
        agent_response = data.get("agent_response")

        log(f"  [Turn {turn}] Agent: {agent_reply[:80]}...", verbose)
        log(f"  [Turn {turn}] State: {state}", verbose)

        log_entries.append({
            "turn": turn,
            "tenant": tenant_msg,
            "agent": agent_reply,
            "state": state,
            "agent_response": agent_response,
        })

        # Check terminal states
        if state in ("closed", "ticket_created", "escalated_to_human", "agent_disabled"):
            break

        # Feed agent reply back to persona LLM
        llm_messages.append({"role": "user", "content": f"[Agent replied]: {agent_reply}"})
        llm_messages.append({"role": "user", "content": "Generate your next message as the tenant."})

        try:
            tenant_msg = generate_tenant_message(client, llm_messages, model)
        except Exception as e:
            log(f"  [Turn {turn}] Tenant generation failed: {e}", verbose)
            break

    return log_entries


def run_multi_phase_scenario(
    client: OpenAI,
    scenario: dict,
    phone: str,
    model: str,
    verbose: bool,
) -> list[dict]:
    """Run a multi-phase scenario (e.g. create ticket → manage it).

    Each phase runs as a separate conversation on the same phone number.
    The backend reopens the conversation when it receives a message after close.
    """
    all_entries = []
    phases = scenario["phases"]

    for phase_idx, phase in enumerate(phases):
        phase_num = phase_idx + 1
        log(f"\n  ── Phase {phase_num}/{len(phases)} ──", verbose)

        persona_prompt = PERSONA_SYSTEM_TEMPLATE.format(
            persona=phase["persona"],
            language=scenario["language"],
        )
        llm_messages = [{"role": "system", "content": persona_prompt}]
        terminal_states = phase.get("terminal_states", ["closed"])

        # Generate initial tenant message for this phase
        tenant_msg = generate_tenant_message(client, llm_messages, model)

        for turn in range(1, MAX_TURNS + 1):
            global_turn = len(all_entries) + 1
            log(f"  [P{phase_num} T{turn}] Tenant: {tenant_msg[:80]}...", verbose)

            try:
                r = requests.post(
                    f"{BASE}/webhook/test",
                    json={"phone": phone, "message": tenant_msg},
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.Timeout:
                log(f"  [P{phase_num} T{turn}] TIMEOUT", verbose)
                all_entries.append({
                    "turn": global_turn, "phase": phase_num, "tenant": tenant_msg,
                    "agent": "[TIMEOUT]", "state": "error", "agent_response": None,
                })
                break

            if r.status_code != 200:
                log(f"  [P{phase_num} T{turn}] HTTP {r.status_code}: {r.text[:100]}", verbose)
                all_entries.append({
                    "turn": global_turn, "phase": phase_num, "tenant": tenant_msg,
                    "agent": f"[HTTP {r.status_code}]", "state": "error", "agent_response": None,
                })
                break

            data = r.json()
            agent_reply = data.get("reply", "")
            state = data.get("state", "")
            agent_response = data.get("agent_response")

            log(f"  [P{phase_num} T{turn}] Agent: {agent_reply[:80]}...", verbose)
            log(f"  [P{phase_num} T{turn}] State: {state}", verbose)

            all_entries.append({
                "turn": global_turn, "phase": phase_num, "tenant": tenant_msg,
                "agent": agent_reply, "state": state, "agent_response": agent_response,
            })

            if state in terminal_states or state in ("agent_disabled", "error"):
                break

            llm_messages.append({"role": "user", "content": f"[Agent replied]: {agent_reply}"})
            llm_messages.append({"role": "user", "content": "Generate your next message as the tenant."})

            try:
                tenant_msg = generate_tenant_message(client, llm_messages, model)
            except Exception as e:
                log(f"  [P{phase_num} T{turn}] Tenant generation failed: {e}", verbose)
                break

    return all_entries


# ── Log Writing ──────────────────────────────────────────────────────────────

def write_log(scenario: dict, phone: str, entries: list[dict], output_dir: Path, started: datetime):
    """Write conversation log to a .txt file."""
    finished = datetime.now()
    duration = int((finished - started).total_seconds())
    final_state = entries[-1]["state"] if entries else "no_turns"

    path = output_dir / f"{scenario['id']}.txt"
    lines = [
        f"SCENARIO: {scenario['id']} — {scenario['name']}",
        f"PHONE: {phone}",
        f"TENANT: {scenario['tenant_name']} (apt {scenario['apartment']})",
        f"EXPECTED STATES: {', '.join(scenario['expected_states'])}",
        f"STARTED: {started.strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 50,
        "",
    ]

    for entry in entries:
        lines.append(f"[Turn {entry['turn']}] STATE: {entry['state']}")
        lines.append(f"TENANT: {entry['tenant']}")
        lines.append(f"AGENT: {entry['agent']}")
        if entry.get("agent_response"):
            ar = entry["agent_response"]
            if isinstance(ar, dict):
                parts = []
                if ar.get("scenario"):
                    parts.append(f"scenario={ar['scenario']}")
                if ar.get("subtype"):
                    parts.append(f"subtype={ar['subtype']}")
                if ar.get("requires_human"):
                    parts.append("requires_human=true")
                if parts:
                    lines.append(f"DETAILS: {', '.join(parts)}")
                # Log tool calls
                tc = ar.get("tools_called")
                if tc and isinstance(tc, list):
                    for tool in tc:
                        if isinstance(tool, dict):
                            name = tool.get("name", "?")
                            args = tool.get("args", {})
                            result = tool.get("result", {})
                            args_str = ", ".join(f"{k}={v}" for k, v in args.items()) if args else ""
                            status = result.get("status", "") if isinstance(result, dict) else ""
                            lines.append(f"TOOL: {name}({args_str}) → {status}")
        lines.append("---")
        lines.append("")

    lines.append("=" * 50)
    lines.append(f"FINISHED: {finished.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"FINAL STATE: {final_state}")
    lines.append(f"TOTAL TURNS: {len(entries)}")
    lines.append(f"DURATION: {duration}s")

    # Check if expected state was reached
    reached = final_state in scenario["expected_states"]
    lines.append(f"EXPECTED STATE REACHED: {'YES' if reached else 'NO'}")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path, final_state, reached


# ── Analyzer ─────────────────────────────────────────────────────────────────

def run_analysis(client: OpenAI, logs_dir: Path, output_path: Path, model: str, verbose: bool):
    """Read all logs and produce an LLM quality analysis."""
    log("\n" + "=" * 60, verbose)
    log("  Running analysis...", verbose)
    log("=" * 60, verbose)

    all_logs = []
    for log_file in sorted(logs_dir.glob("*.txt")):
        content = log_file.read_text(encoding="utf-8")
        all_logs.append(f"--- {log_file.name} ---\n{content}")

    if not all_logs:
        log("  No log files found, skipping analysis.", verbose)
        return

    combined = "\n\n".join(all_logs)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": ANALYZER_SYSTEM},
                {"role": "user", "content": f"Here are 10 conversation logs to analyze:\n\n{combined}"},
            ],
            temperature=0.3,
            max_completion_tokens=4000,
        )
        analysis = response.choices[0].message.content.strip()
    except Exception as e:
        analysis = f"Analysis failed: {e}"
        log(f"  Analysis LLM call failed: {e}", verbose)

    output_path.write_text(analysis, encoding="utf-8")
    log(f"\n  Analysis written to {output_path}", verbose)
    log("\n" + analysis, verbose)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    global BASE
    parser = argparse.ArgumentParser(description="AI Agent scenario tests")
    parser.add_argument("--base-url", default=DEFAULT_BASE, help="Backend API URL")
    parser.add_argument("--model", default=TENANT_MODEL, help="Model for tenant simulation")
    parser.add_argument("--analyzer-model", default=ANALYZER_MODEL, help="Model for analysis")
    parser.add_argument("--scenarios", type=int, default=len(SCENARIOS), help="Number of scenarios to run")
    parser.add_argument("--start", type=int, default=1, help="Start from scenario N (1-based)")
    parser.add_argument("--keep", action="store_true", help="Don't delete test tenants after run")
    parser.add_argument("--no-analysis", action="store_true", help="Skip the LLM analysis step")
    parser.add_argument("--verbose", action="store_true", help="Print conversation to stdout")
    args = parser.parse_args()

    BASE = args.base_url

    # Check OPENAI_TOKEN
    api_key = os.getenv("OPENAI_TOKEN", "")
    if not api_key:
        print("ERROR: OPENAI_TOKEN environment variable not set")
        sys.exit(1)

    client = OpenAI(api_key=api_key)
    start_idx = max(0, args.start - 1)
    scenarios_to_run = SCENARIOS[start_idx:args.scenarios]

    print(f"\n{'='*60}")
    print(f"  AI Agent Scenario Tests")
    print(f"  Scenarios: {len(scenarios_to_run)} | Model: {args.model}")
    print(f"{'='*60}")

    # ── Setup ────────────────────────────────────────────────────────
    print("\n  Setup...")
    token = None
    admin_creds = [
        ("admin@cosmopolis.com", "admin123"),
        ("alisher@cosmorent.kz", "alisher123"),
    ]
    for email, pwd in admin_creds:
        try:
            token = api_login(email, pwd)
            log(f"  Logged in as {email}", args.verbose)
            break
        except Exception:
            continue
    if not token:
        print("  ERROR: Could not login with any admin credentials")
        sys.exit(1)

    try:
        building_id = get_or_create_building(token)
        log(f"  Using building_id={building_id}", args.verbose)
    except Exception as e:
        print(f"  ERROR: Building setup failed: {e}")
        sys.exit(1)

    # Generate unique phone suffix per run to avoid stale conversation state
    run_suffix = str(int(time.time()))[-6:]  # last 6 digits of epoch
    phones = [f"{TEST_PHONE_PREFIX}{run_suffix}{i:02d}" for i in range(1, len(scenarios_to_run) + 1)]
    log(f"  Run suffix: {run_suffix} (phones: {phones[0]}–{phones[-1]})", args.verbose)

    tenant_ids = setup_tenants(token, building_id, scenarios_to_run, phones, args.verbose)
    print(f"  {len(tenant_ids)} tenants ready")

    # Create output directories
    output_base = Path(__file__).parent / "agent_scenarios"
    logs_dir = output_base / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # ── Run scenarios ────────────────────────────────────────────────
    results = []
    total_start = time.time()

    for i, scenario in enumerate(scenarios_to_run, 1):
        phone = phones[i - 1]

        print(f"\n{'─'*60}")
        print(f"  [{i}/{len(scenarios_to_run)}] {scenario['name']}")
        print(f"  Phone: {phone} | Tenant: {scenario['tenant_name']}")
        print(f"{'─'*60}")

        started = datetime.now()
        try:
            if scenario.get("multi_phase"):
                entries = run_multi_phase_scenario(client, scenario, phone, args.model, args.verbose)
            else:
                entries = run_scenario(client, scenario, phone, args.model, args.verbose)
            log_path, final_state, reached = write_log(scenario, phone, entries, logs_dir, started)
            duration = int((datetime.now() - started).total_seconds())

            status = "PASS" if reached else "WARN"
            print(f"  Result: {status} | State: {final_state} | Turns: {len(entries)} | {duration}s")
            print(f"  Log: {log_path}")

            results.append({
                "scenario": scenario["id"],
                "name": scenario["name"],
                "status": status,
                "final_state": final_state,
                "turns": len(entries),
                "duration": duration,
                "reached_expected": reached,
            })
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({
                "scenario": scenario["id"],
                "name": scenario["name"],
                "status": "ERROR",
                "final_state": "error",
                "turns": 0,
                "duration": 0,
                "reached_expected": False,
            })

    # ── Analysis ─────────────────────────────────────────────────────
    if not args.no_analysis:
        analysis_path = output_base / "analysis.txt"
        run_analysis(client, logs_dir, analysis_path, args.analyzer_model, args.verbose)

    # ── Summary ──────────────────────────────────────────────────────
    total_time = int(time.time() - total_start)
    passed = sum(1 for r in results if r["reached_expected"])
    warned = sum(1 for r in results if r["status"] == "WARN")
    errors = sum(1 for r in results if r["status"] == "ERROR")

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    for r in results:
        icon = "✓" if r["reached_expected"] else ("✗" if r["status"] == "ERROR" else "⚠")
        print(f"  {icon} {r['scenario']}: {r['final_state']} ({r['turns']} turns, {r['duration']}s)")
    print(f"\n  Total: {passed} passed, {warned} warned, {errors} errors")
    print(f"  Time: {total_time}s ({total_time // 60}m {total_time % 60}s)")
    print(f"  Logs: {logs_dir}")
    if not args.no_analysis:
        print(f"  Analysis: {output_base / 'analysis.txt'}")

    # ── Cleanup ──────────────────────────────────────────────────────
    if not args.keep:
        print("\n  Cleanup...")
        cleanup_tenants(token, tenant_ids, args.verbose)
        print("  Done.")
    else:
        print("\n  Skipping cleanup (--keep)")

    sys.exit(1 if errors > 0 else 0)


if __name__ == "__main__":
    main()
