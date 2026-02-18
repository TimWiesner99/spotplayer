#!/usr/bin/env bash
# setup.sh — SpotPlayer first-time setup for Ubuntu 24.04 LXC
#
# Run as root (or with sudo) on a fresh Ubuntu 24.04 LXC container.
# After running this script:
#   1. Edit /home/spotplayer/app/.env  (set SECRET_KEY and PASSWORD_HASH)
#   2. Restart the service: systemctl restart spotplayer
#
# What this script does:
#   - Installs system packages (Nginx, FFmpeg, Python 3)
#   - Creates a dedicated 'spotplayer' system user
#   - Clones / copies the application to /home/spotplayer/app
#   - Creates a Python virtualenv and installs dependencies
#   - Creates the media storage directory
#   - Installs a systemd service for uvicorn
#   - Configures Nginx (X-Accel-Redirect + proxy to uvicorn)

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────
APP_USER="spotplayer"
APP_DIR="/home/${APP_USER}/app"
# MEDIA_ROOT: where video files, thumbnails, and upload chunks live.
# Mount your media drive at this path before running setup.
# Must match the MEDIA_ROOT value in .env.
MEDIA_DIR="/data"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"   # directory containing this script

echo "==> SpotPlayer setup starting"
echo "    Source: ${REPO_DIR}"
echo "    Install: ${APP_DIR}"

# ── System packages ────────────────────────────────────────────────────────
echo "==> Installing system packages…"
apt-get update -qq
apt-get install -y --no-install-recommends \
    nginx \
    ffmpeg \
    python3 \
    curl

# ── Application user ──────────────────────────────────────────────────────
echo "==> Creating application user '${APP_USER}'…"
if ! id "${APP_USER}" &>/dev/null; then
    useradd --system --create-home --shell /bin/false "${APP_USER}"
fi

# ── Copy application files ─────────────────────────────────────────────────
echo "==> Copying application files to ${APP_DIR}…"
mkdir -p "${APP_DIR}"
# Rsync everything except .git, __pycache__, .env, and the venv.
rsync -a --exclude='.git' --exclude='__pycache__' --exclude='.env' \
         --exclude='.venv' --exclude='*.pyc' \
         "${REPO_DIR}/" "${APP_DIR}/"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

# ── Media storage directory ────────────────────────────────────────────────
# /data is expected to be a pre-mounted drive (root-owned by default).
# We create the required subdirectories and set permissions so the app user
# can write files and Nginx (www-data) can read them for X-Accel-Redirect.
echo "==> Preparing media directory ${MEDIA_DIR}…"
if [ ! -d "${MEDIA_DIR}" ]; then
    echo "ERROR: ${MEDIA_DIR} does not exist. Mount your media drive first." >&2
    exit 1
fi
mkdir -p "${MEDIA_DIR}/projects" "${MEDIA_DIR}/uploads"
# Give the app user ownership of the media subdirectories only.
chown -R "${APP_USER}:${APP_USER}" "${MEDIA_DIR}/projects" "${MEDIA_DIR}/uploads"
# 755: app user can write; nginx (www-data) and others can read and traverse.
chmod -R 755 "${MEDIA_DIR}/projects" "${MEDIA_DIR}/uploads"

# ── uv (Python package manager) ────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
    echo "==> Installing uv…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add to PATH for the rest of this script.
    export PATH="$HOME/.local/bin:$PATH"
fi

# ── Python virtualenv and dependencies ────────────────────────────────────
echo "==> Syncing Python dependencies with uv…"
cd "${APP_DIR}"
sudo -u "${APP_USER}" bash -c "cd ${APP_DIR} && uv sync"

# ── .env file ─────────────────────────────────────────────────────────────
if [ ! -f "${APP_DIR}/.env" ]; then
    echo "==> Creating .env from .env.example…"
    cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"

    # Generate a random SECRET_KEY automatically.
    SECRET_KEY=$(uv run python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null \
                 || python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s|change-this-to-a-random-hex-string|${SECRET_KEY}|" "${APP_DIR}/.env"

    chown "${APP_USER}:${APP_USER}" "${APP_DIR}/.env"
    chmod 600 "${APP_DIR}/.env"

    echo ""
    echo "  *** ACTION REQUIRED ***"
    echo "  Set the PASSWORD_HASH in ${APP_DIR}/.env"
    echo "  Generate one with:"
    echo "    uv run python3 -c \"import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())\""
    echo ""
fi

# ── systemd service ────────────────────────────────────────────────────────
echo "==> Installing systemd service…"
cat > /etc/systemd/system/spotplayer.service << EOF
[Unit]
Description=SpotPlayer video review application
After=network.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
# uv run resolves the virtualenv created by uv sync automatically.
ExecStart=/home/${APP_USER}/.local/bin/uv run uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=5s
# Give FFmpeg transcoding jobs time to finish before killing the process.
TimeoutStopSec=60s

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable spotplayer

# ── Nginx configuration ────────────────────────────────────────────────────
echo "==> Configuring Nginx…"

# Update the static files path in nginx.conf to match APP_DIR.
NGINX_CONF="/etc/nginx/sites-available/spotplayer"
cp "${APP_DIR}/nginx.conf" "${NGINX_CONF}"
# Replace the repo-relative static path with the actual install path.
sed -i "s|/home/tim/repos/spotplayer/static/|${APP_DIR}/static/|g" "${NGINX_CONF}"

# Disable default Nginx site and enable SpotPlayer.
rm -f /etc/nginx/sites-enabled/default
ln -sf "${NGINX_CONF}" /etc/nginx/sites-enabled/spotplayer

nginx -t   # validate config before reloading
systemctl reload nginx

# ── Start the service ──────────────────────────────────────────────────────
echo "==> Starting SpotPlayer service…"
systemctl restart spotplayer

echo ""
echo "==> Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Set PASSWORD_HASH in ${APP_DIR}/.env"
echo "  2. Run: systemctl restart spotplayer"
echo "  3. Visit http://<LXC-IP>/ to verify"
echo ""
echo "Useful commands:"
echo "  systemctl status spotplayer      # check service status"
echo "  journalctl -u spotplayer -f      # follow logs"
echo "  systemctl reload nginx           # reload nginx after config changes"
