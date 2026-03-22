# Prompts — LLM System Prompt Templates

## Files
```
prompts/
├── agent.txt                  # Main agent system prompt (personality, workflow, tools)
├── escalation.txt             # Dispatcher notification template (LLM-generated)
├── technician_assignment.txt  # Technician notification template (LLM-generated)
├── router.txt                 # Deprecated — old intent classifier prompt
└── writer.txt                 # Deprecated — old response writer prompt
```

## Active Prompts
- **agent.txt** — loaded by `agent/llm.py` at startup. Defines the agent persona, service workflow, category/urgency mappings, tool usage patterns, and language handling.
- **escalation.txt** — used by `services/notifier.py` to generate dispatcher group chat messages on escalation.
- **technician_assignment.txt** — used by `services/notifier.py` to generate WhatsApp messages to technicians when assigned a ticket.

## Deprecated
- **router.txt** / **writer.txt** — from the old two-step classifier pipeline (`services/classifier.py`). Kept for reference but no longer used in the active agent flow.
