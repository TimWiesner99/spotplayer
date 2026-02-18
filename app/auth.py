"""
Authentication utilities for SpotPlayer.

Design:
  - Single shared password, stored as a bcrypt hash in .env.
  - Successful login sets a signed, HttpOnly cookie using itsdangerous.
  - The cookie payload is simply {"authenticated": True}; no per-user data.

FUTURE AUTH HOOK:
  To swap in OAuth/SSO (e.g. Authentik, Auth0, Google):
  1. Remove `verify_password` / cookie logic.
  2. Add an OAuth redirect flow in app/routes/auth.py.
  3. Replace the body of `is_authenticated()` with token/session validation
     from your IdP (e.g. verify a JWT from the Authorization header or a
     provider-issued session cookie).
  The downstream route handlers and the `require_api_auth` dependency stay
  unchanged — they all call `is_authenticated()`.
"""

import bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, Response, HTTPException

from app.config import settings

# Cookie / session constants
SESSION_COOKIE_NAME = "spotplayer_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7  # 7 days
_SALT = "spotplayer-session-v1"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_serializer() -> URLSafeTimedSerializer:
    """Return a configured itsdangerous serializer."""
    return URLSafeTimedSerializer(settings.secret_key, salt=_SALT)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def verify_password(plain_password: str) -> bool:
    """
    Verify *plain_password* against the bcrypt hash in settings.
    Uses bcrypt's constant-time comparison to prevent timing attacks.
    """
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            settings.password_hash.encode("utf-8"),
        )
    except Exception:
        # Malformed hash in .env — treat as failure, never crash.
        return False


def set_session_cookie(response: Response) -> None:
    """Sign and attach a session cookie to *response*."""
    serializer = _get_serializer()
    token = serializer.dumps({"authenticated": True})
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,   # not accessible from JS
        samesite="lax",  # CSRF mitigation
        secure=False,    # Set True if terminating TLS at this app (we use NPM)
    )


def clear_session_cookie(response: Response) -> None:
    """Remove the session cookie (used on logout)."""
    response.delete_cookie(SESSION_COOKIE_NAME)


def is_authenticated(request: Request) -> bool:
    """
    Return True if the request carries a valid, unexpired session cookie.

    FUTURE AUTH HOOK: replace this body with your IdP token validation.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return False
    try:
        serializer = _get_serializer()
        data = serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
        return bool(data.get("authenticated"))
    except (BadSignature, SignatureExpired):
        return False


def require_api_auth(request: Request) -> None:
    """
    FastAPI dependency for JSON/API endpoints.
    Raises HTTP 401 if the caller is not authenticated.

    Usage:
        @router.post("/api/...")
        async def my_endpoint(_: None = Depends(require_api_auth)):
            ...
    """
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
