# Routers — FastAPI Route Handlers

Each file defines an `APIRouter` mounted in `main.py` under `/api`.

## Files
```
routers/
├── tickets.py        # /api/tickets — CRUD, pagination, notes, photo, XLSX export
├── technicians.py    # /api/technicians — tech management, schedules, workload, my-tickets
├── conversations.py  # /api/conversations — WhatsApp conversation history, media
├── webhook.py        # /api/webhook — test endpoint + Green API production webhook
├── users.py          # /api/users — user CRUD (admin-only)
├── agents.py         # /api/agents — building & tenant management (admin/agent)
└── analytics.py      # /api/analytics — dashboard stats (admin/owner)
```

## Auth Patterns
- `get_current_user` — any authenticated user
- `get_admin_user` — admin only
- `get_dispatcher_user` — admin or dispatcher
- `get_owner_user` — admin or owner
- `get_agent_user` — admin or agent
- Role checks defined in `auth.py`

## Key Conventions
- Ticket IDs: `TKT-XXXXXXXX` (ticket_number) or integer (internal id)
- Technician endpoints use UUID string IDs
- Pagination: `skip`/`limit` query params
- Full API documentation: see `API.md` in project root
