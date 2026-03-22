# Tests — Integration & Scenario Tests

## Files
```
tests/
├── test_api.py                # REST API integration tests (all endpoints, all roles)
├── test_agent_scenarios.py    # LLM-driven agent conversation scenarios
└── agent_scenarios/
    ├── logs/                  # Per-scenario conversation logs (.txt)
    └── analysis.txt           # LLM quality analysis of scenario runs
```

## Running

### API Integration Tests
```bash
# Backend must be running
source .env && python3 backend/tests/test_api.py
```

### Agent Scenario Tests
```bash
# Runs LLM-simulated tenants against the AI agent
source .env && python3 backend/tests/test_agent_scenarios.py --scenarios 3 --verbose
```

Options:
- `--scenarios N` — run first N scenarios (default: all 15)
- `--start N` — start from scenario N (1-based)
- `--verbose` — print conversation to stdout
- `--keep` — don't delete test tenants after run
- `--no-analysis` — skip LLM quality analysis step

## Scenario Coverage
15 scenarios covering: plumbing happy path, emergency electrical, heating with date preference, appliance repair, FAQ, billing inquiry, cancellation mid-flow, escalation request, multi-language, vague complaint, reschedule ticket, cancel ticket, add comment, second ticket creation, cancel mid-reschedule.

Each scenario defines a tenant persona with specific messages, and validates the final conversation state (closed, escalated_to_human, etc.).
