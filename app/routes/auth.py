"""
Authentication routes: login and logout.

GET  /login  — render the login form
POST /login  — validate password, set session cookie, redirect to /
POST /logout — clear session cookie, redirect to /login
"""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import verify_password, set_session_cookie, clear_session_cookie, is_authenticated

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    """Render the login form. Redirect to home if already authenticated."""
    if is_authenticated(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": error},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    password: str = Form(...),
):
    """
    Validate the submitted password against the bcrypt hash in settings.
    On success: set session cookie and redirect to /.
    On failure: re-render login form with an error message.
    """
    if not verify_password(password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Incorrect password. Try again."},
            status_code=401,
        )

    # Password correct — create a signed session cookie and send the user home.
    response = RedirectResponse("/", status_code=302)
    set_session_cookie(response)
    return response


@router.post("/logout")
async def logout(request: Request):
    """Clear the session cookie and redirect to the login page."""
    response = RedirectResponse("/login", status_code=302)
    clear_session_cookie(response)
    return response
