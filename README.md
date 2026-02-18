# SpotPlayer

Web-based player for reviewing interview footage. Upload a video and an SRT subtitle file; the transcript is displayed alongside the video and stays highlighted in sync with playback. Clicking anywhere in the transcript jumps the video to that point.

---

## Server startup guide

### Prerequisites

- Ubuntu 24.04 (LXC or bare-metal)
- `ffmpeg` installed (`sudo apt install ffmpeg`)
- `uv` installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- A media drive mounted at `/data`
- Nginx running and configured (see `nginx.conf`)

### One-time setup

**1. Prepare the media directory**

```bash
sudo mkdir -p /data/projects /data/uploads
sudo chown -R tim:tim /data/projects /data/uploads
```

**2. Install Python dependencies**

```bash
cd /home/tim/repos/spotplayer
uv sync
```

**3. Configure the application**

```bash
cp .env.example .env
```

Edit `.env` and set:

- `SECRET_KEY` — a long random string:
  ```bash
  uv run python3 -c "import secrets; print(secrets.token_hex(32))"
  ```
- `PASSWORD_HASH` — bcrypt hash of your chosen access password:
  ```bash
  uv run python3 -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"
  ```
- `MEDIA_ROOT` — defaults to `/data`. Change only if your drive is mounted elsewhere.

**4. Configure Nginx**

Copy `nginx.conf` to `/etc/nginx/sites-available/spotplayer`, update the
`alias` path under `location /internal/media/` if `MEDIA_ROOT` is not `/data`,
then enable it:

```bash
sudo cp nginx.conf /etc/nginx/sites-available/spotplayer
sudo ln -s /etc/nginx/sites-available/spotplayer /etc/nginx/sites-enabled/spotplayer
sudo nginx -t && sudo systemctl reload nginx
```

### Starting the server

```bash
cd /home/tim/repos/spotplayer
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

For production (keeps the server running after logout):

```bash
# Using the systemd service installed by setup.sh:
sudo systemctl start spotplayer
sudo systemctl enable spotplayer   # start automatically on boot

# Check logs:
journalctl -u spotplayer -f
```

### Stopping the server

```bash
sudo systemctl stop spotplayer
# or if running in the foreground: Ctrl-C
```

---

## User guide

### Accessing the app

Open a browser and navigate to the server's address (e.g. `https://spotplayer.yourdomain.com`). You will be prompted for the shared password.

### Uploading an interview

1. Click **Upload new** on the home page.
2. Enter a project title (e.g. `Interview — Jane Doe 2025-01-15`).
3. Select the **.srt subtitle file** for that recording.
4. Select the **video file** (MP4, MKV, MOV, AVI, WebM — any format FFmpeg supports).
5. Click **Start upload**.

The video is sent in 50 MB chunks (compatible with Cloudflare's free plan). A progress bar tracks each chunk. When the upload finishes the server transcodes the video to H.264 MP4 in the background if needed, and extracts a thumbnail. This can take a few minutes for large files.

### Reviewing footage

The project appears on the home page once processing is complete (the spinner disappears and a thumbnail is shown).

Click the project to open the viewer:

- **Left panel** — standard video player with playback controls.
- **Right panel** — full transcript as flowing prose.

While the video plays, the currently spoken phrase is **highlighted in yellow** and kept in view automatically.

Click any word or sentence in the transcript to **jump the video** to that point. Playback starts automatically.

---

## Configuration reference

All settings live in `.env` (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | *(required)* | Signs session cookies |
| `PASSWORD_HASH` | *(required)* | bcrypt hash of the access password |
| `MEDIA_ROOT` | `/data` | Where videos and thumbnails are stored |
| `NGINX_INTERNAL_PREFIX` | `/internal/media` | Internal Nginx location for media serving |
| `DATABASE_PATH` | `./spotplayer.db` | SQLite database file |
