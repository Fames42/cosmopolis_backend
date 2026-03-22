# Cosmopolis

AI-powered property management system. Tenants submit maintenance requests via WhatsApp, an AI agent triages and classifies them, and dispatchers/technicians manage tickets through a web dashboard.

## Project Structure

```
cosmopolis/
├── backend/              # FastAPI REST API (Docker) → see backend/CLAUDE.md
│   ├── src/              # Python source → see backend/src/CLAUDE.md
│   │   ├── agent/        # Agentic LLM engine → see backend/src/agent/CLAUDE.md
│   │   ├── routers/      # API route handlers → see backend/src/routers/CLAUDE.md
│   │   ├── services/     # Business logic → see backend/src/services/CLAUDE.md
│   │   └── alembic/      # DB migrations → see backend/src/alembic/CLAUDE.md
│   ├── prompts/          # LLM prompt templates → see backend/prompts/CLAUDE.md
│   ├── tests/            # Integration & scenario tests → see backend/tests/CLAUDE.md
│   ├── backups/          # Daily DB snapshots
│   ├── analytics/        # WhatsApp data extraction → see backend/analytics/CLAUDE.md
│   ├── Dockerfile
│   └── docker-compose.yml
├── start-dev.sh          # Start both backend (Docker) + frontend (npm)
├── .env                  # Environment variables (WhatsApp API keys, OpenAI token)
├── API.md                # Full REST API documentation
└── CLAUDE.md             # This file
```

## Quick Start

### Both services (recommended)
```bash
./start-dev.sh          # starts Docker backend + npm frontend
```

### Backend only (Docker)
```bash
docker compose -f backend/docker-compose.yml up --build -d
```

### Frontend only
```bash
cd frontend
npm run dev    # http://localhost:3000
```

## Test Credentials
- Admin: `admin@cosmopolis.com` / `admin123`
- Owner: `owner@cosmopolis.com` / `owner123`
- Dispatcher: `dispatcher@cosmopolis.com` / `dispatcher123`
- Technician: `tech@cosmopolis.com` / `tech123`
- Agent: `agent@cosmopolis.com` / `agent123`

## Key Conventions

- **Roles**: admin, owner, dispatcher, technician, agent — enforced via JWT + RBAC
- **Technicians**: universal masters — no specialization, any technician handles any category
- **Ticket statuses**: new → assigned → scheduled → done | cancelled
- **Ticket categories**: plumbing, electrical, heating, appliance, structural, other
- **Urgency levels**: low, medium, high, emergency
- **Conversation states**: new_conversation → gathering → classified_* → scenario handlers → managing_ticket → closed
- **AI scenarios**: service, faq, billing, announcement, unknown
- **Ports**: backend on 8000, frontend on 3000
- **Timezone**: UTC+5 (Almaty/Astana) for all scheduling
- **API docs**: Auto-generated at `/docs` (Swagger) and `/redoc`; full reference in `API.md`

## AI Agent

The AI agent (GPT-5.4) uses an agentic loop with tool calls (OpenAI Responses API):
1. Receives WhatsApp message → buffers rapid-fire messages (5s window)
2. Runs LLM with conversation history + system prompt + 10 tools
3. LLM calls tools (update details, search slots, create ticket, escalate, etc.)
4. Engine executes tools, returns results to LLM for next step
5. Final reply is sent back to tenant via WhatsApp

Tools: `update_service_details`, `search_available_slots`, `select_time_slot`, `create_ticket`, `escalate_to_human`, `close_conversation`, `lookup_my_tickets`, `reschedule_ticket`, `add_ticket_comment`, `cancel_ticket`

Test via: `POST /api/webhook/test` with `{"phone": "...", "message": "..."}`

## Important Notes

- Backend runs via Docker (PostgreSQL + FastAPI via gunicorn/uvicorn)
- CORS is currently open (`*`) — restrict for production
- JWT secret is hardcoded in `backend/src/auth.py` — move to env var for production
- Do not commit `.env` — it contains API secrets (OPENAI_TOKEN, etc.)
- Database changes require Alembic migrations (see `backend/src/alembic/CLAUDE.md`)
