# Backend Source — FastAPI REST API

## Tech Stack
- **Framework**: FastAPI
- **ORM**: SQLAlchemy (PostgreSQL via Docker)
- **Auth**: JWT (python-jose) + OAuth2 password bearer + bcrypt (passlib)
- **Validation**: Pydantic schemas
- **AI**: OpenAI GPT-5.4 (via `openai` SDK, Responses API)
- **Server**: gunicorn + uvicorn workers

## File Layout
```
src/
├── main.py          # App setup, CORS, route registration
├── database.py      # SQLAlchemy engine, SessionLocal, Base
├── models.py        # ORM models (User, Ticket, Building, Tenant, Conversation, Message)
├── schemas.py       # Pydantic request/response schemas
├── auth.py          # JWT creation/verification, RBAC helpers, password hashing
├── agent/           # Agentic LLM engine → see agent/CLAUDE.md
├── routers/         # API route handlers → see routers/CLAUDE.md
├── services/        # Business logic & integrations → see services/CLAUDE.md
└── alembic/         # Database migrations → see alembic/CLAUDE.md
```

## Key Enums (models.py)
- `RoleEnum`: admin, owner, dispatcher, technician, agent
- `TicketStatusEnum`: new, assigned, scheduled, done, cancelled
- `ConversationStatusEnum`: open, closed
- `ConversationStateEnum`: new_conversation, gathering, classified_service, classified_faq, classified_billing, classified_announcement, service_collecting_details, service_assessing_urgency, service_scheduling, service_ready_for_ticket, ticket_created, technician_assigned, managing_ticket, escalated_to_human, closed
- `ScenarioEnum`: service, faq, billing, announcement, unknown
- `MessageSenderEnum`: tenant, ai, admin
- `MessageTypeEnum`: text, image, video, audio, document, mixed

## Key Patterns

- **Primary keys**: UUID (string) for users, Integer for other entities
- **Technicians**: universal — no specialization, any technician handles any category
- **Role-based access**: `get_current_user` dependency + role checks in each endpoint
- **Pagination**: `skip` / `limit` query params on list endpoints
- **Error responses**: 401 (auth), 403 (permissions), 404 (not found)
- **Relationships**: User → Ticket (assigned_to), Building → Tenant → Conversation → Message

## AI Agent Flow
1. `webhook.py` receives message → buffers rapid-fire messages (5s window)
2. `orchestrator.handle_message()` delegates to `AgentEngine.run()`
3. Engine loads conversation state + history via protocol adapters
4. Calls LLM with system prompt + tools; LLM returns tool calls
5. Engine executes tools (update details, search slots, create ticket, escalate, etc.)
6. Loop continues until LLM produces final text reply
7. Reply is saved and returned; WhatsApp notification sent

## API Route Prefixes
- `/api/auth` — login
- `/api/tickets` — ticket operations
- `/api/technicians` — technician management
- `/api/conversations` — WhatsApp data
- `/api/users` — user management
- `/api/analytics` — dashboard stats
- `/api/agents` — building & tenant management
- `/api/webhook` — AI agent test endpoint + Green API webhook (no auth)
