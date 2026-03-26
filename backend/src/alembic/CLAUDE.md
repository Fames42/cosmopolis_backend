# Alembic — Database Migrations

## Structure
```
alembic/
├── env.py           # Migration runner config (loads ORM models, DATABASE_URL)
└── versions/        # Sequential migration scripts
    ├── 001_add_tenant_agent_enabled.py
    ├── 002_drop_phone_unique.py
    ├── 003_add_tenant_company.py
    ├── 004_add_managing_ticket_state.py
    ├── 005_rename_tenant_lease_columns.py
    ├── 006_drop_user_specialties.py
    ├── 007_add_user_is_head.py
    ├── 008_add_tenant_category.py
    └── 009_add_building_detail_columns.py
```

## Conventions
- Revision IDs: sequential 3-digit strings (`"001"`, `"002"`, ...)
- File naming: `NNN_short_description.py`
- Each migration has `upgrade()` and `downgrade()` functions
- Migrations run automatically on container startup via `main.py`

## Creating a New Migration
1. Add the new file in `versions/` following the naming pattern
2. Set `revision` and `down_revision` to chain correctly
3. Use `op.add_column()`, `op.drop_column()`, `op.alter_column()`, etc.
4. Rebuild container: `docker compose -f backend/docker-compose.yml up --build -d`
