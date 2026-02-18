"""
Project listing (home page) and project status/list APIs.

GET  /                          — home page: project grid
GET  /api/projects              — JSON list of all projects (used by sidebar.js)
GET  /api/project/{id}/status   — JSON status poll for in-progress processing
POST /api/project/{id}/reprocess — re-queue processing for an errored project
"""

import aiosqlite
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import is_authenticated, require_api_auth
from app.database import get_db
from app.tasks.processing import reprocess_video

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


@router.get("/api/projects")
async def projects_list(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    _: None = Depends(require_api_auth),
):
    """
    Return all projects as a JSON array for the sidebar.
    Newest first; includes only the fields the sidebar needs.
    """
    cursor = await db.execute(
        """
        SELECT id, title, thumbnail_path, upload_timestamp, processing_status
        FROM   projects
        ORDER  BY upload_timestamp DESC
        """
    )
    rows = await cursor.fetchall()
    return JSONResponse([dict(r) for r in rows])


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


@router.post("/api/project/{project_id}/reprocess")
async def reprocess_project(
    project_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: aiosqlite.Connection = Depends(get_db),
    _: None = Depends(require_api_auth),
):
    """
    Re-queue video processing for a project that previously errored.

    Requires the assembled source file to still exist in the project directory
    (it is preserved even when processing fails). Does not require re-uploading.

    Only allowed when status is 'error'; prevents double-queuing on 'processing'.
    """
    cursor = await db.execute(
        "SELECT processing_status FROM projects WHERE id = ?", (project_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if row["processing_status"] == "processing":
        raise HTTPException(status_code=409, detail="Already processing")
    if row["processing_status"] != "error":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reprocess a project with status '{row['processing_status']}'"
        )

    # Reset status to pending so the UI shows a spinner immediately.
    await db.execute(
        "UPDATE projects SET processing_status = 'pending' WHERE id = ?",
        (project_id,),
    )
    await db.commit()

    background_tasks.add_task(reprocess_video, project_id=project_id)
    return JSONResponse({"status": "queued"})
