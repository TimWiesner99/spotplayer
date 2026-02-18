"""
SpotPlayer configuration.

All settings are loaded from environment variables (or a .env file).
Add new config knobs here — never hardcode values in route handlers.
"""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Signing key for session cookies — must be a long random secret.
    secret_key: str

    # bcrypt hash of the shared access password.
    # Generate: python -c "import bcrypt; print(bcrypt.hashpw(b'pw', bcrypt.gensalt()).decode())"
    password_hash: str

    # Root directory where all media files (videos, thumbnails, temp chunks) are stored.
    # Nginx must be able to read files under this path for X-Accel-Redirect to work.
    media_root: Path = Path("/var/lib/spotplayer/media")

    # The Nginx internal location prefix that maps to media_root.
    # Must match the `location` block in nginx.conf (see nginx.conf).
    nginx_internal_prefix: str = "/internal/media"

    # SQLite database file path.
    database_path: str = "./spotplayer.db"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

# Derived paths — computed once at startup.
PROJECTS_DIR = settings.media_root / "projects"
UPLOADS_DIR = settings.media_root / "uploads"
