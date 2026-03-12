# Cosmopolis

AI-powered property management system. Tenants submit maintenance requests via WhatsApp, an AI agent triages and classifies them, and dispatchers/technicians manage tickets through a web dashboard.

## Project Structure

```
cosmopolis/
├── backend/       # FastAPI REST API (Python, SQLAlchemy, JWT auth) → see backend/CLAUDE.md
├── frontend/      # Next.js 16 dashboard (React 19, TypeScript, Tailwind 4) → see frontend/CLAUDE.md
├── .env           # Environment variables (WhatsApp API keys, OpenAI token)
└── CLAUDE.md      # This file
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
- **Ports**: backend on 8000, frontend on 3000
- **API docs**: Auto-generated at `/docs` (Swagger) and `/redoc`

## Important Notes

- Frontend stores JWT token and role in `localStorage`
- CORS is currently open (`*`) — restrict for production
- JWT secret is hardcoded in `backend/auth.py` — move to env var for production
- SQLite is used for dev; switch to PostgreSQL for production via `backend/database.py`
- Do not commit `.env` — it contains API secrets
