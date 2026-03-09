# Cosmopolis Agent

AI-powered property management maintenance request system. Tenants submit requests via WhatsApp, an AI agent triages and classifies them, and dispatchers/technicians manage tickets through a web dashboard.

## Project Structure

```
cosmopolis_agent/
├── backend/       # FastAPI REST API (Python, SQLAlchemy, JWT auth)
├── frontend/      # Next.js 16 dashboard (React 19, TypeScript, Tailwind 4)
├── analytics/     # WhatsApp data extraction scripts (GreenAPI)
├── rules.txt      # AI system prompt for maintenance router
├── cosmopolis.db  # SQLite database (dev)
└── .env           # Environment variables (WhatsApp API keys, OpenAI token)
```

## Quick Start

### Backend
```bash
source .venv/bin/activate
uvicorn backend.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm run dev    # http://localhost:3000
```

### Seed Database
```bash
python -m backend.seed_db
```

## Test Credentials
- Admin: `admin@cosmopolis.com` / `admin123`
- Owner: `owner@cosmopolis.com` / `owner123`
- Dispatcher: `dispatcher@cosmopolis.com` / `dispatcher123`
- Technician: `tech@cosmopolis.com` / `tech123`

## Key Conventions

- **Roles**: admin, owner, dispatcher, technician — enforced via JWT + RBAC
- **Ticket statuses**: new → assigned → scheduled → done | cancelled
- **Ticket categories**: plumbing, electrical, heating, appliance, structural, other
- **Urgency levels**: low, medium, high, emergency
- **API docs**: Auto-generated at `/docs` (Swagger) and `/redoc`

## Important Notes

- Backend API runs on port 8000, frontend on port 3000
- Frontend stores JWT token and role in localStorage
- CORS is currently open (`*`) — restrict for production
- JWT secret is hardcoded in `backend/auth.py` — move to env var for production
- SQLite is used for dev; switch to PostgreSQL for production via `backend/database.py`
- Do not commit `.env` — it contains API secrets
