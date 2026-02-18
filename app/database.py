"""
Database initialization and connection management.

Uses aiosqlite for async SQLite access. Each request opens its own connection
via the `get_db` dependency (FastAPI Depends). The schema is created on startup
via `init_db()` called from the lifespan handler in main.py.

Tables:
  projects       — one row per uploaded project (video + transcript).
  cues           — parsed SRT cue records linked to a project.
  upload_sessions — tracks in-progress chunked video uploads.
"""

import aiosqlite
from app.config import settings

_DB_PATH = settings.database_path


async def get_db() -> aiosqlite.Connection:
    """
    FastAPI dependency: yields an open aiosqlite connection with Row factory set.
    Use as `db: aiosqlite.Connection = Depends(get_db)` in route handlers.
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Enforce foreign key constraints (disabled by default in SQLite).
        await db.execute("PRAGMA foreign_keys = ON")
        yield db


async def init_db() -> None:
    """Create all tables if they don't exist. Called once at application startup."""
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")

        # --- projects --------------------------------------------------------
        await db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                title            TEXT    NOT NULL,
                -- Paths are stored relative to settings.media_root so the DB is
                -- portable if the mount point changes.
                video_path       TEXT,
                srt_path         TEXT,
                thumbnail_path   TEXT,
                upload_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                -- pending | processing | ready | error
                processing_status TEXT NOT NULL DEFAULT 'pending'
            )
        """)

        # --- cues ------------------------------------------------------------
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cues (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                index_num  INTEGER NOT NULL,
                start_time REAL    NOT NULL,  -- seconds (float)
                end_time   REAL    NOT NULL,  -- seconds (float)
                text       TEXT    NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
        """)
        # Index to speed up time-range lookups in the viewer API.
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_cues_project_time
            ON cues (project_id, start_time)
        """)

        # --- upload_sessions -------------------------------------------------
        await db.execute("""
            CREATE TABLE IF NOT EXISTS upload_sessions (
                id           TEXT    PRIMARY KEY,  -- UUID
                project_id   INTEGER NOT NULL,
                total_chunks INTEGER NOT NULL,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
        """)

        await db.commit()
