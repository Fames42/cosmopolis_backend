# Backend — FastAPI REST API

## Tech Stack
- **Framework**: FastAPI
- **ORM**: SQLAlchemy (SQLite dev, PostgreSQL-ready)
- **Auth**: JWT (python-jose) + OAuth2 password bearer + bcrypt (passlib)
- **Validation**: Pydantic schemas
- **Server**: Uvicorn

## Running
```bash
source ../.venv/bin/activate
uvicorn backend.main:app --reload --port 8000
python -m backend.seed_db   # seed test data
```

## File Layout
```
backend/
├── main.py          # App setup, CORS, route registration, static mount
├── database.py      # SQLAlchemy engine, SessionLocal, Base
├── models.py        # ORM models (User, Ticket, Building, Tenant, Conversation, Message)
├── schemas.py       # Pydantic request/response schemas
├── auth.py          # JWT creation/verification, RBAC helpers, password hashing
├── seed_db.py       # Database seeder with test users & sample data
└── routers/
    ├── tickets.py       # Ticket CRUD, pagination, notes, filtering
    ├── technicians.py   # Technician management, my-tickets, status updates
    ├── conversations.py # WhatsApp conversation retrieval (no technician access)
    ├── users.py         # User CRUD (admin-only)
    └── analytics.py     # Summary stats for owners
```

## Key Patterns

- **Primary keys**: UUID for users, Integer for other entities
- **Role-based access**: Use `get_current_user` dependency + role checks in each endpoint
- **Pagination**: `skip` / `limit` query params on list endpoints
- **Error responses**: 401 (auth), 403 (permissions), 404 (not found)
- **Enums**: `RoleEnum`, `TicketStatusEnum`, `ConversationStatusEnum` defined in models.py
- **Relationships**: User → Ticket (assigned_to), Building → Tenant → Conversation → Message

## Auth Flow
1. POST `/api/auth/login` with email + password
2. Returns `{ access_token, token_type, role }`
3. All protected endpoints require `Authorization: Bearer {token}` header
4. Token expiry: 7 days

## API Route Prefixes
- `/api/auth` — login
- `/api/tickets` — ticket operations
- `/api/technicians` — technician management
- `/api/conversations` — WhatsApp data
- `/api/users` — user management
- `/api/analytics` — dashboard stats
