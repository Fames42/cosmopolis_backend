# Agent — Agentic LLM Engine

Tool-calling agent loop using OpenAI Responses API. Receives tenant messages, runs LLM with tools, executes tool calls, and returns a final reply.

## Files
```
agent/
├── engine.py      # AgentEngine — main agentic loop, tool execution
├── llm.py         # OpenAILLMClient — LLM calls, tool definitions, response parsing
├── protocols.py   # Protocol interfaces (ConversationStore, SchedulingService, etc.)
├── types.py       # DTOs and enums (ConversationState, SlotInfo, AgentResult, etc.)
└── context.py     # ConversationContext — per-conversation mutable state container
```

## Architecture

- **Protocol-based**: engine depends on abstract protocols, not concrete DB/ORM code
- **Adapters** (`services/adapters.py`) bridge SQLAlchemy models to agent protocols
- **Tool loop**: LLM → tool_calls → engine executes → results fed back → LLM continues

## Tools (defined in llm.py)
- `update_service_details` — set category, urgency, description, danger flag
- `search_available_slots` — find technician availability (optional date/time preference)
- `select_time_slot` — book a specific slot from offered options
- `create_ticket` — finalize and persist the ticket
- `escalate_to_human` — route to dispatcher with reason
- `close_conversation` — end the conversation
- `lookup_my_tickets` — list tenant's active tickets
- `reschedule_ticket` — change appointment time
- `add_ticket_comment` — append info to existing ticket
- `cancel_ticket` — cancel an existing ticket

## Key Classes
- `AgentEngine` (engine.py) — `run()` method is the main entry point
- `OpenAILLMClient` (llm.py) — wraps OpenAI Responses API, manages `previous_response_id` for continuity
- `ConversationContext` (context.py) — holds tenant info, state, context_data dict, escalation status
