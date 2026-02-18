"""
Viewer page route.

GET /project/{project_id}

Renders the split-layout viewer: video player on the left, flowing-prose
transcript on the right. All cue data is embedded directly in the rendered
HTML so the page works without a second API round-trip.
"""

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import is_authenticated
from app.database import get_db

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/project/{project_id}", response_class=HTMLResponse)
async def viewer(
    project_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    Render the viewer for a specific project.

    The transcript cues are fetched and embedded in the page template so the
    JavaScript only needs to set up event listeners — no additional API calls.
    """
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=302)

    # --- Fetch project metadata -------------------------------------------
    cursor = await db.execute(
        "SELECT id, title, video_path, processing_status FROM projects WHERE id = ?",
        (project_id,),
    )
    project = await cursor.fetchone()
    if project is None:
        return HTMLResponse("<h1>Project not found</h1>", status_code=404)

    # --- Fetch ordered cues -----------------------------------------------
    cursor = await db.execute(
        """
        SELECT index_num, start_time, end_time, text
        FROM   cues
        WHERE  project_id = ?
        ORDER  BY start_time ASC
        """,
        (project_id,),
    )
    rows = await cursor.fetchall()
    cues = [dict(r) for r in rows]

    return templates.TemplateResponse(
        "viewer.html",
        {
            "request": request,
            "project": dict(project),
            "cues": cues,
        },
    )
