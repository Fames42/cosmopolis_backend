# Cosmopolis Backend

FastAPI REST API with AI-powered WhatsApp agent. Runs in Docker (PostgreSQL + gunicorn/uvicorn).

## Project Structure

```
backend/
├── src/                # Python package (module name: src)
│   ├── main.py         # App setup, CORS, route registration
│   ├── database.py     # SQLAlchemy engine, SessionLocal, Base
│   ├── models.py       # ORM models + enums
│   ├── schemas.py      # Pydantic request/response schemas
│   ├── auth.py         # JWT + RBAC + password hashing
│   ├── agent/          # Agentic LLM engine (tool-calling loop)
│   ├── routers/        # API route handlers
│   ├── services/       # Business logic, scheduling, notifications
│   └── alembic/        # Database migrations
├── prompts/            # LLM prompt templates (agent, escalation, assignment)
├── tests/              # Integration & scenario tests
├── backups/            # Automated daily DB snapshots (.sql.gz)
├── analytics/          # WhatsApp data extraction scripts
├── Dockerfile
└── docker-compose.yml
```

## Running (Docker)

```bash
docker compose up --build -d                              # start
docker compose logs backend --tail 50                     # logs
docker compose down                                       # stop
```

## Test Credentials
- Admin: `admin@cosmopolis.com` / `admin123`
- Owner: `owner@cosmopolis.com` / `owner123`
- Dispatcher: `dispatcher@cosmopolis.com` / `dispatcher123`
- Technician: `tech@cosmopolis.com` / `tech123`
- Agent: `agent@cosmopolis.com` / `agent123`

## Key Conventions

- **Module path**: `src.*` (not `backend.*`) — the Dockerfile copies `src/` into `/app/src/`
- **Roles**: admin, owner, dispatcher, technician, agent — `RoleEnum` in models.py
- **Technicians**: universal masters — no specialization filtering
- **Ticket statuses**: new → assigned → scheduled → done | cancelled
- **Conversation states**: new_conversation → gathering → classified_* → ... → managing_ticket → closed
- **AI scenarios**: service, faq, billing, announcement, unknown
- **Ports**: API on 8000, PostgreSQL on 5433 (host) / 5432 (container)
- **Timezone**: UTC+5 (Almaty/Astana) for all scheduling

## AI Agent

- **Engine**: `src/agent/engine.py` — agentic loop using OpenAI Responses API with tool calls
- **LLM client**: `src/agent/llm.py` — GPT-5.4 via OpenAI API, defines 10 tools
- **System prompt**: `prompts/agent.txt` — loaded at startup, cached
- **Orchestrator** (`services/orchestrator.py`): thin shim delegating to `AgentEngine`
- **Adapters** (`services/adapters.py`): bridges SQLAlchemy ORM to agent protocol interfaces
- **Test endpoint**: `POST /api/webhook/test` with `{"phone": "...", "message": "..."}`

## Environment Variables

Required in `.env` (project root):
- `OPENAI_TOKEN` — OpenAI API key for GPT-5.4
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` — DB credentials (defaults in docker-compose.yml)

## Important Notes

- Database: PostgreSQL in Docker (dev), managed via Alembic migrations
- CORS is open (`*`) — restrict for production
- JWT secret is hardcoded in `auth.py` — move to env var for production
