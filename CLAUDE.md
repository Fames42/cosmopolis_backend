# Cosmopolis

AI-powered property management system. Tenants submit maintenance requests via WhatsApp, an AI agent triages and classifies them, and dispatchers/technicians manage tickets through a web dashboard.

## Project Structure

```
cosmopolis/
├── backend/          # FastAPI REST API (Docker) → see backend/CLAUDE.md
│   ├── src/          # Python source → see backend/src/CLAUDE.md
│   ├── rules.txt     # AI agent system prompt
│   ├── Dockerfile
│   └── docker-compose.yml
├── frontend/         # Next.js 16 dashboard → see frontend/CLAUDE.md
├── start-dev.sh      # Start both backend (Docker) + frontend (npm)
├── .env              # Environment variables (WhatsApp API keys, OpenAI token)
└── CLAUDE.md         # This file
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
- **Ticket statuses**: new → assigned → scheduled → done | cancelled
- **Ticket categories**: plumbing, electrical, heating, appliance, structural, other
- **Urgency levels**: low, medium, high, emergency
- **Conversation states**: new_conversation → gathering → classified_* → scenario handlers → closed
- **AI scenarios**: service, faq, billing, announcement, unknown
- **Ports**: backend on 8000, frontend on 3000
- **API docs**: Auto-generated at `/docs` (Swagger) and `/redoc`
- **Frontend i18n**: Russian translations via `frontend/src/lib/translations.ts`

## AI Agent

The AI agent (GPT-5.4) handles WhatsApp conversations with tenants:
1. Greets and asks how it can help (auto-detects language)
2. Gathers context across multiple messages (`gathering` state)
3. Classifies intent into scenario (service/faq/billing/announcement)
4. Escalates to dispatcher if confidence < 0.65 or `requires_human`

Test via: `POST /api/webhook/test` with `{"phone": "...", "message": "..."}`

## Important Notes

- Backend runs via Docker (PostgreSQL + FastAPI via gunicorn/uvicorn)
- Frontend stores JWT token and role in `localStorage`
- CORS is currently open (`*`) — restrict for production
- JWT secret is hardcoded in `backend/src/auth.py` — move to env var for production
- Do not commit `.env` — it contains API secrets (OPENAI_TOKEN, etc.)
