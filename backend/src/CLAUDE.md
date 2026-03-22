# Backend Source — FastAPI REST API

## Tech Stack
- **Framework**: FastAPI
- **ORM**: SQLAlchemy (PostgreSQL via Docker)
- **Auth**: JWT (python-jose) + OAuth2 password bearer + bcrypt (passlib)
- **Validation**: Pydantic schemas
- **AI**: OpenAI GPT-5.4 (via `openai` SDK)
- **Server**: gunicorn + uvicorn workers

## File Layout
```
src/
├── main.py          # App setup, CORS, route registration
├── database.py      # SQLAlchemy engine, SessionLocal, Base
├── models.py        # ORM models (User, Ticket, Building, Tenant, Conversation, Message)
├── schemas.py       # Pydantic request/response schemas
├── auth.py          # JWT creation/verification, RBAC helpers, password hashing
├── routers/
│   ├── tickets.py       # Ticket CRUD, pagination, notes, filtering
│   ├── technicians.py   # Technician management, my-tickets, status updates
│   ├── conversations.py # WhatsApp conversation retrieval
│   ├── users.py         # User CRUD (admin-only)
│   ├── analytics.py     # Summary stats for owners
│   ├── agents.py        # Agent-related endpoints
│   └── webhook.py       # Test endpoint for AI agent (POST /api/webhook/test)
└── services/
    ├── __init__.py
    ├── classifier.py    # LLM conversational agent — reply + classification
    └── orchestrator.py  # Conversation state machine — routes messages through states
```

## Key Enums (models.py)
- `RoleEnum`: admin, owner, dispatcher, technician, agent
- `TicketStatusEnum`: new, assigned, scheduled, done, cancelled
- `ConversationStatusEnum`: open, closed
- `ConversationStateEnum`: new_conversation, gathering, classified_service, classified_faq, classified_billing, classified_announcement, service_collecting_details, service_assessing_urgency, service_scheduling, service_ready_for_ticket, ticket_created, technician_assigned, escalated_to_human, closed
- `ScenarioEnum`: service, faq, billing, announcement, unknown
- `MessageSenderEnum`: tenant, ai, admin
- `MessageTypeEnum`: text, image, video, audio, document, mixed

## Key Patterns

- **Primary keys**: UUID (string) for users, Integer for other entities
- **Role-based access**: `get_current_user` dependency + role checks in each endpoint
- **Pagination**: `skip` / `limit` query params on list endpoints
- **Error responses**: 401 (auth), 403 (permissions), 404 (not found)
- **Relationships**: User → Ticket (assigned_to), Building → Tenant → Conversation → Message

## AI Agent Flow
1. `webhook.py` receives message → calls `orchestrator.handle_message()`
2. Orchestrator finds/creates Conversation, saves tenant message
3. If state is `new_conversation` or `gathering` → calls `classifier.process_message()`
4. Classifier sends conversation history (last 20 messages) + system prompt to GPT-5.4
5. GPT returns JSON: `{reply, classified, scenario, confidence, subtype, requires_human}`
6. Orchestrator transitions state based on classification result
7. AI reply is saved and returned

## API Route Prefixes
- `/api/auth` — login
- `/api/tickets` — ticket operations
- `/api/technicians` — technician management
- `/api/conversations` — WhatsApp data
- `/api/users` — user management
- `/api/analytics` — dashboard stats
- `/api/agents` — agent management
- `/api/webhook` — AI agent test endpoint (no auth required)
