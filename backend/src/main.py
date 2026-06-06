import os
import socket
import logging
from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .routers import users, tickets, conversations, analytics, technicians, agents, webhook
from .auth import router as auth_router
from .database import engine
from .services import reminders

logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="Cosmopolis Agent API")


def log_alembic_state():
    """Log migration state without mutating the production database."""
    try:
        project_root = Path(__file__).resolve().parents[1]
        alembic_cfg = Config(str(project_root / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(project_root / "src" / "alembic"))
        script = ScriptDirectory.from_config(alembic_cfg)
        head_revision = script.get_current_head()

        with engine.connect() as conn:
            current_revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()

        if current_revision == head_revision:
            logger.info("Alembic revision is current: %s", current_revision)
        else:
            logger.warning(
                "Alembic revision mismatch: database=%s code_head=%s",
                current_revision or "none",
                head_revision,
            )
    except Exception:
        logger.exception("Could not read Alembic revision state")


@app.on_event("startup")
def log_access_info():
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except socket.gaierror:
        local_ip = "unknown"
    port = os.getenv("PORT", "8000")
    logger.info("=" * 50)
    logger.info(f"Cosmopolis API started")
    logger.info(f"Hostname:  {hostname}")
    logger.info(f"Local IP:  {local_ip}")
    logger.info(f"Access:    http://{local_ip}:{port}")
    logger.info(f"Docs:      http://{local_ip}:{port}/docs")
    logger.info("=" * 50)
    log_alembic_state()


@app.on_event("startup")
async def start_ticket_reminders():
    reminders.start_ticket_reminder_loop()


@app.on_event("shutdown")
async def stop_ticket_reminders():
    await reminders.stop_ticket_reminder_loop()

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # For production, restrict this.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(tickets.router, prefix="/api/tickets", tags=["tickets"])
app.include_router(conversations.router, prefix="/api/conversations", tags=["conversations"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(technicians.router, prefix="/api/technicians", tags=["technicians"])
app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(webhook.router, prefix="/api/webhook", tags=["webhook"])

# Serve the frontend application (skip in Docker where frontend is deployed separately)
if os.path.isdir("frontend"):
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
