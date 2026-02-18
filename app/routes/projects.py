"""
Project listing (home page) and project status API.

GET /           — home page: list all projects with thumbnail + status
GET /api/project/{id}/status — JSON status poll used by the frontend while
                               a video is processing
"""

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import is_authenticated, require_api_auth
from app.database import get_db

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """
    Render the project browser.

    Projects are listed newest-first. The template polls /api/project/{id}/status
    for any project whose status is not 'ready' or 'error'.
    """
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=302)

    cursor = await db.execute(
        """
        SELECT id, title, thumbnail_path, upload_timestamp, processing_status
        FROM   projects
        ORDER  BY upload_timestamp DESC
        """
    )
    rows = await cursor.fetchall()
    # Convert Row objects to dicts so Jinja2 can access them easily.
    projects = [dict(r) for r in rows]

    return templates.TemplateResponse(
        "home.html",
        {"request": request, "projects": projects},
    )


@router.get("/api/project/{project_id}/status")
async def project_status(
    project_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    _: None = Depends(require_api_auth),
):
    """
    Return the current processing_status of a project as JSON.

    The frontend polls this endpoint every few seconds for projects that are
    still being processed, and refreshes the page when status becomes 'ready'.

    Response:  {"status": "pending"|"processing"|"ready"|"error"}
    """
    cursor = await db.execute(
        "SELECT processing_status FROM projects WHERE id = ?",
        (project_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return JSONResponse({"error": "Project not found"}, status_code=404)

    return JSONResponse({"status": row["processing_status"]})
