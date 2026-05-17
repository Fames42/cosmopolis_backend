# AGENTS.md

Guidance for AI coding agents working in this repository.

## Project Overview

Cosmopolis is a FastAPI backend for an AI-powered property management system. Tenants send maintenance requests over WhatsApp, an OpenAI-powered agent triages and schedules them, and staff manage tickets through REST APIs.

This checkout contains the backend. Some docs/scripts mention a `frontend/` directory, but it is ignored and not present here.

## Repository Layout

```text
.
|-- API.md                         # REST API reference
|-- start-dev.sh                   # Starts backend Docker services, then a separate frontend if present
|-- backend/
|   |-- Dockerfile
|   |-- docker-compose.yml         # PostgreSQL, backend, backup service
|   |-- requirements.txt
|   |-- alembic.ini
|   |-- prompts/                   # Active and legacy LLM prompt templates
|   |-- src/
|   |   |-- main.py                # FastAPI app and route registration
|   |   |-- database.py            # SQLAlchemy engine/session/Base
|   |   |-- models.py              # ORM models and enums
|   |   |-- schemas.py             # Pydantic schemas
|   |   |-- auth.py                # JWT auth and RBAC helpers
|   |   |-- agent/                 # Agentic OpenAI Responses API loop
|   |   |-- routers/               # FastAPI route handlers
|   |   |-- services/              # Business logic and integrations
|   |   `-- alembic/               # Alembic env and migrations
|   |-- tests/                     # Script-style integration/scenario tests
|   |-- analytics/                 # Green API extraction scripts and data
|   `-- backups/                   # Database dump snapshots
`-- CLAUDE.md                      # Older project guidance; prefer this file for agent work
```

## Core Stack

- Python 3.12 in Docker.
- FastAPI, SQLAlchemy 2.x, Pydantic 2.x.
- PostgreSQL in Docker for normal development; `database.py` falls back to local SQLite if `DATABASE_URL` is unset.
- JWT auth with `python-jose` and password hashing via `passlib`/bcrypt.
- OpenAI SDK with the Responses API for the active agent loop.
- Alembic for schema migrations.

## Run Commands

From the repository root:

```bash
docker compose -f backend/docker-compose.yml up --build -d
docker compose -f backend/docker-compose.yml logs backend --tail 50
docker compose -f backend/docker-compose.yml down
```

The API listens on `http://localhost:8000`; docs are at `/docs` and `/redoc`.

`./start-dev.sh` also tries to run `cd frontend && npm run dev`. Use it only when a frontend checkout exists.

## Test Commands

Tests are script-style and expect a live backend on `localhost:8000`; they are not a normal pytest suite.

```bash
source .env
python3 backend/tests/test_api.py
```

Agent scenario tests call OpenAI and can rewrite files under `backend/tests/agent_scenarios/`:

```bash
source .env
python3 backend/tests/test_agent_scenarios.py --scenarios 3 --verbose
```

Useful scenario options:

- `--scenarios N`: run the first N scenarios.
- `--start N`: start from scenario N, 1-based.
- `--keep`: leave generated test tenants in the database.
- `--no-analysis`: skip LLM quality analysis.
- `--base-url URL`: target a non-default backend URL.

Do not run networked or LLM-backed tests unless the task needs them and the required environment is available.

## Environment And Secrets

The backend reads `.env` from the repository root through Docker Compose. Common variables include:

- `OPENAI_TOKEN`
- `POSTGRES_PASSWORD`
- `SECRET_KEY`
- `ID_INSTANCE`
- `API_TOKEN_INSTANCE`

Do not print, copy, commit, or expose `.env` values. The file may exist locally even though it is ignored.

## Domain Conventions

- Roles: `admin`, `owner`, `dispatcher`, `technician`, `agent`.
- Head technicians (`User.is_head`) receive dispatcher-level access in some RBAC paths.
- Ticket statuses: `new`, `assigned`, `scheduled`, `done`, `cancelled`.
- Ticket categories: `plumbing`, `electrical`, `heating`, `appliance`, `structural`, `other`.
- Urgency levels: `low`, `medium`, `high`, `emergency`.
- Conversation states and scenarios are defined in `backend/src/models.py`.
- Ticket numbers use the `TKT-XXXXXXXX` format; some endpoints also accept internal integer IDs.
- Technicians are universal: do not reintroduce specialty filtering unless explicitly requested.
- Scheduling uses the local UTC+5 Almaty/Astana business timezone in service logic.

## Backend Architecture

- Module imports should use `src.*`, not `backend.*`; Docker copies `backend/src` into `/app/src`.
- `backend/src/main.py` registers routers under `/api`.
- Routers should stay thin: request validation, auth, response shaping, and delegation.
- Business behavior belongs in `backend/src/services/`.
- ORM and enum changes belong in `backend/src/models.py`; API contracts belong in `backend/src/schemas.py`.
- Keep Pydantic schema changes synchronized with router responses and frontend/API expectations.
- CORS is currently open in `main.py`; preserve existing behavior unless the task is specifically about production hardening.

## AI Agent Architecture

The active tenant assistant is the agentic loop in `backend/src/agent/`:

- `engine.py`: main `AgentEngine.run()` loop and tool execution.
- `llm.py`: OpenAI client, Responses API calls, tool definitions, prompt loading.
- `protocols.py`: abstract interfaces used by the engine.
- `types.py`: DTOs and agent enums.
- `context.py`: mutable per-conversation context.

`backend/src/services/adapters.py` bridges SQLAlchemy models to the agent protocols. `services/orchestrator.py` is a compatibility shim around the new engine. `services/classifier.py`, `prompts/router.txt`, and `prompts/writer.txt` are legacy paths; avoid building new behavior on them.

Active prompts:

- `backend/prompts/agent.txt`
- `backend/prompts/escalation.txt`
- `backend/prompts/technician_assignment.txt`

When changing agent behavior, update the prompt, tool schema, engine handling, adapters, and scenario tests together as needed.

## Database And Migrations

- Alembic config is in `backend/alembic.ini`; migration scripts live in `backend/src/alembic/versions/`.
- Existing revision IDs are sequential three-digit strings (`001`, `002`, ...).
- New migrations should follow `NNN_short_description.py` and correctly set `revision` and `down_revision`.
- Alembic `env.py` reads `DATABASE_URL` when available.
- Do not assume migrations are automatically applied by the app; verify the startup path before relying on that behavior.
- Avoid editing generated backup dumps in `backend/backups/`.

Example commands from the repository root:

```bash
docker compose -f backend/docker-compose.yml exec backend alembic upgrade head
docker compose -f backend/docker-compose.yml exec backend alembic current
```

## API And Auth Notes

- Full endpoint reference is in `API.md`.
- Auth routes are mounted under `/api/auth`.
- Main resource prefixes are `/api/tickets`, `/api/technicians`, `/api/conversations`, `/api/users`, `/api/analytics`, `/api/agents`, and `/api/webhook`.
- `/api/webhook/test` is the local agent test endpoint; production webhook handling is also in `routers/webhook.py`.
- Standard test credentials are documented in `CLAUDE.md`; avoid adding real credentials to docs or tests.

## Analytics And Generated Data

`backend/analytics/` contains Green API scripts and cached/extracted WhatsApp data. Treat chat exports and conversation files as sensitive operational data. Do not modify or regenerate analytics output unless the task specifically asks for it.

Scenario tests can generate or rewrite:

- `backend/tests/agent_scenarios/logs/`
- `backend/tests/agent_scenarios/analysis.txt`

Check `git status` after running them and keep unrelated generated churn out of focused patches.

## Coding Guidelines

- Follow existing FastAPI dependency and SQLAlchemy session patterns.
- Prefer explicit, small service functions over adding router-level business logic.
- Keep enum/string values stable unless a migration and API compatibility plan are part of the task.
- For scheduling or ticket mutations, preserve timezone handling, technician universality, and active-ticket filtering.
- For OpenAI calls, keep model and API behavior centralized in `agent/llm.py` or the existing notifier helpers.
- Add concise comments only where they clarify non-obvious business rules.
- Keep unrelated refactors, formatting churn, and generated data changes out of patches.

## Before Finishing

- Run the narrowest relevant checks that are practical in the current environment.
- If tests require Docker, a live backend, network access, or OpenAI credentials and cannot be run, say so clearly.
- Recheck `git status --short` and mention any pre-existing unrelated changes separately from your own edits.
