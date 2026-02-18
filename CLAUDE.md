# SpotPlayer — Developer Reference

Web-based interview footage review tool. Upload a video + SRT subtitle file,
then watch with the transcript highlighted word-by-word in sync with playback.
Clicking any part of the transcript seeks the video to that point.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + uvicorn (Python 3.11+) |
| Package manager | uv (replaces pip + venv) |
| Database | SQLite via aiosqlite (async) |
| Auth | bcrypt password hash + itsdangerous signed cookie |
| Video processing | FFmpeg via ffmpeg-python |
| Templates | Jinja2 |
| Frontend | Vanilla HTML / CSS / JS (no framework) |
| Media serving | Nginx X-Accel-Redirect (inside LXC) |
| External access | Nginx Proxy Manager on Proxmox host → LXC port 80 |

---

## Repository layout

```
spotplayer/
├── main.py                   # FastAPI app entry point, lifespan hooks
├── requirements.txt
├── .env.example              # Copy to .env and fill in secrets
├── nginx.conf                # Nginx config snippet for the LXC
├── setup.sh                  # First-time setup on Ubuntu 24.04
│
├── app/
│   ├── config.py             # Pydantic settings — all env vars live here
│   ├── database.py           # aiosqlite init + get_db dependency
│   ├── auth.py               # Password verify, session cookie, auth dependency
│   ├── srt_parser.py         # SRT file parser → list of cue dicts
│   │
│   ├── routes/
│   │   ├── auth.py           # GET/POST /login, POST /logout
│   │   ├── projects.py       # GET / (home), GET /api/project/{id}/status
│   │   ├── upload.py         # GET /upload, POST /api/upload/{init,chunk,finalize}
│   │   ├── viewer.py         # GET /project/{id}
│   │   └── media.py          # GET /api/media/{video,thumbnail}/{id}
│   │
│   └── tasks/
│       └── processing.py     # Background: assemble chunks → FFmpeg → DB update
│
├── templates/
│   ├── base.html             # Navbar, common head
│   ├── login.html            # Standalone (no base) login page
│   ├── home.html             # Project grid with status polling
│   ├── upload.html           # Chunked upload form
│   └── viewer.html           # Split-layout player + transcript
│
└── static/
    ├── css/main.css          # Single flat stylesheet
    ├── js/upload.js          # Chunked upload client
    └── js/viewer.js          # timeupdate listener + binary-search cue highlight
```

---

## Configuration (.env)

All runtime config lives in `.env` (loaded by `app/config.py` via pydantic-settings).

| Variable | Description |
|---|---|
| `SECRET_KEY` | Random hex string for signing session cookies |
| `PASSWORD_HASH` | bcrypt hash of the shared access password |
| `MEDIA_ROOT` | Absolute path where media files are stored (default `/data` — mount your media drive here) |
| `NGINX_INTERNAL_PREFIX` | Internal Nginx location prefix for X-Accel-Redirect (default `/internal/media`) |
| `DATABASE_PATH` | SQLite file path (default `./spotplayer.db`) |

Generate a password hash:
```bash
uv run python3 -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"
```

Generate a secret key:
```bash
uv run python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## Database schema

### `projects`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| title | TEXT | User-supplied project name |
| video_path | TEXT | Relative to `MEDIA_ROOT` |
| srt_path | TEXT | Relative to `MEDIA_ROOT` |
| thumbnail_path | TEXT | Relative to `MEDIA_ROOT` |
| upload_timestamp | DATETIME | Defaults to `CURRENT_TIMESTAMP` |
| processing_status | TEXT | `pending` → `processing` → `ready` \| `error` |

### `cues`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| project_id | INTEGER FK | → `projects.id` CASCADE DELETE |
| index_num | INTEGER | Original SRT cue number |
| start_time | REAL | Seconds (float) |
| end_time | REAL | Seconds (float) |
| text | TEXT | HTML-stripped, whitespace-normalised |

### `upload_sessions`
| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | UUID |
| project_id | INTEGER FK | → `projects.id` |
| total_chunks | INTEGER | Expected chunk count |
| created_at | DATETIME | |

---

## Upload flow (chunked)

Cloudflare free plan caps individual HTTP requests at 100 MB. The client
splits video files into 50 MB chunks and uploads them sequentially.

```
Client                               Server
  │                                    │
  │── POST /api/upload/init ──────────►│  create project row, parse SRT, open session
  │◄─ {upload_id, project_id} ─────────│
  │                                    │
  │── POST /api/upload/chunk (×N) ────►│  write chunk_{i:05d} to uploads/{session_id}/
  │◄─ {received: i} ───────────────────│
  │                                    │
  │── POST /api/upload/finalize ──────►│  verify all chunks present
  │◄─ {project_id} ────────────────────│  → BackgroundTask: assemble + FFmpeg + DB
```

---

## Video serving (X-Accel-Redirect)

Browser seeking requires HTTP Range requests. Nginx handles these natively;
uvicorn does not. The chain is:

```
Browser GET /api/media/video/{id}
  → FastAPI: check auth cookie → return empty body + X-Accel-Redirect: /internal/media/projects/{id}/video.mp4
  → Nginx: sees X-Accel-Redirect, serves file from /var/lib/spotplayer/media/projects/{id}/video.mp4
    with full Range support
```

The `/internal/media/` Nginx location is marked `internal` — direct browser
requests to that path get a 404.

---

## Auth hook (future SSO)

The single-password auth is designed to be swapped out:

1. All auth logic lives in `app/auth.py`.
2. Route handlers call `is_authenticated(request)` (page routes) or use the
   `require_api_auth` dependency (API routes).
3. To add OAuth/SSO: replace the body of `is_authenticated()` with your IdP
   token validation. The rest of the code is unchanged.

---

## Background processing

`app/tasks/processing.py::process_video` runs as a FastAPI `BackgroundTask`:

1. Assemble ordered chunks from `UPLOADS_DIR/{session_id}/`.
2. `ffprobe` → check if already H.264/MP4.
3. If not, `ffmpeg` transcode (CRF 23, preset medium, `movflags faststart`).
4. Extract JPEG thumbnail at 10 s (frame 0 fallback for short videos).
5. Update `projects.processing_status` + file paths in DB.
6. Delete temp chunk directory.

**Known limitation**: `BackgroundTasks` are in-process. A server restart drops
any in-flight processing job and leaves the project in `processing` status.
For production with many concurrent uploads, replace with Celery + Redis or ARQ.

---

## Running locally (development)

```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies (creates .venv automatically)
uv sync

# Configure
cp .env.example .env
# Edit .env: set SECRET_KEY and PASSWORD_HASH
# For local dev override the media root:
#   MEDIA_ROOT=./media

# Create media dirs (must match MEDIA_ROOT in .env)
mkdir -p /data/projects /data/uploads
# (or: mkdir -p media/projects media/uploads  if using a local ./media override)

# Run
uv run uvicorn main:app --reload --port 8000
```

### /data directory permissions (production)

`/data` is a root-owned mount point. The app user needs write access to its
subdirectories, and Nginx needs read access. Run once as root:

```bash
sudo mkdir -p /data/projects /data/uploads
sudo chown -R tim:tim /data/projects /data/uploads
sudo chmod -R 755 /data/projects /data/uploads
```

If you change the app user (e.g. to `spotplayer` via `setup.sh`), substitute
that username for `tim` above.

For local dev, video serving via X-Accel-Redirect will not work without Nginx.
You can test uploads and the transcript viewer, but video playback will return
an empty response. To test end-to-end, use the production Nginx setup.

---

## Deployment

See `setup.sh` for the automated steps. Manual summary:

```bash
# 1. Install packages
apt-get install nginx ffmpeg python3
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Set up app
cp -r . /home/spotplayer/app
cd /home/spotplayer/app && uv sync

# 3. Configure
cp .env.example .env   # then edit SECRET_KEY + PASSWORD_HASH

# 4. Nginx
cp nginx.conf /etc/nginx/sites-available/spotplayer
ln -s /etc/nginx/sites-available/spotplayer /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# 5. systemd
# (see setup.sh for the service unit file)
systemctl enable --now spotplayer
```

---

## Known limitations / future work

- **Task queue**: BackgroundTasks are in-process; use Celery/ARQ for resilience.
- **Stale upload sessions**: Abandoned uploads leave orphan dirs in `uploads/`.
  Add a cron job to delete sessions older than 24 h.
- **Single password**: No per-user accounts. See auth hook above for SSO path.
- **No transcript editing**: Read-only for now.
- **FFmpeg progress**: Only coarse status (processing/ready) is surfaced.
  Could stream FFmpeg stderr to a WebSocket for a real progress bar.
- **Local dev video**: X-Accel-Redirect requires Nginx; video won't play in
  pure uvicorn dev mode.
