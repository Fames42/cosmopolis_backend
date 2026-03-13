import os
import socket
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .database import engine
from .models import Base
from .routers import users, tickets, conversations, analytics, technicians, agents, webhook
from .auth import router as auth_router

logger = logging.getLogger("uvicorn.error")

# Create the database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Cosmopolis Agent API")


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
