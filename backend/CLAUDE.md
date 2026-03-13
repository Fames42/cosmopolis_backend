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
│   ├── seed_db.py      # Database seeder
│   ├── routers/        # API route handlers
│   └── services/       # AI agent (classifier, orchestrator)
├── rules.txt           # AI agent system prompt
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

## Running (Docker)

```bash
docker compose up --build -d                              # start
docker compose run --rm backend python -m src.seed_db     # seed DB
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
- **Ticket statuses**: new → assigned → scheduled → done | cancelled
- **Conversation states**: new_conversation → gathering → classified_* → ... → closed
- **AI scenarios**: service, faq, billing, announcement, unknown
- **Ports**: API on 8000, PostgreSQL on 5433 (host) / 5432 (container)

## AI Agent

- **System prompt**: `rules.txt` — loaded at startup, cached
- **LLM**: GPT-5.4 via OpenAI API (`OPENAI_TOKEN` env var)
- **Classifier** (`services/classifier.py`): sends conversation history + instruction → returns reply + classification JSON
- **Orchestrator** (`services/orchestrator.py`): state machine — manages conversation lifecycle, calls classifier
- **Test endpoint**: `POST /api/webhook/test` with `{"phone": "...", "message": "..."}`

## Environment Variables

Required in `.env` (project root):
- `OPENAI_TOKEN` — OpenAI API key for GPT-5.4
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` — DB credentials (defaults in docker-compose.yml)

## Important Notes

- Database: PostgreSQL in Docker (dev), tables auto-created on startup
- `seed_db.py` drops and recreates all tables — only use in dev
- CORS is open (`*`) — restrict for production
- JWT secret is hardcoded in `auth.py` — move to env var for production
