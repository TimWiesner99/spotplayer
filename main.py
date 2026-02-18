"""
SpotPlayer — web-based video player with synchronized transcript highlighting.

Entry point. Wires together:
  - FastAPI application instance
  - Startup/shutdown lifespan (DB init, media directory creation)
  - Static file serving for /static/*
  - All route modules

Run with:
    uvicorn main:app --host 127.0.0.1 --port 8000

In production, Nginx proxies port 80 → 8000 and handles X-Accel-Redirect
for media files (see nginx.conf).
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import PROJECTS_DIR, UPLOADS_DIR
from app.database import init_db
from app.routes import auth, media, projects, upload, viewer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan handler.
    Runs setup before the first request and teardown after the last.
    """
    # Ensure required directories exist under MEDIA_ROOT.
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    # Create database tables if they don't exist yet.
    await init_db()

    yield  # application runs here

    # Nothing to clean up on shutdown for now.


app = FastAPI(
    title="SpotPlayer",
    description="Video player with synchronized transcript highlighting for interview review.",
    version="0.1.0",
    # Disable the interactive docs in production if desired.
    # docs_url=None, redoc_url=None,
    lifespan=lifespan,
)

# Serve static assets (CSS, JS) at /static/*.
app.mount("/static", StaticFiles(directory="static"), name="static")

# Register route modules — order doesn't matter for FastAPI, but keep logical.
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(upload.router)
app.include_router(viewer.router)
app.include_router(media.router)
