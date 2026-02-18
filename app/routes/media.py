"""
Authenticated media serving using Nginx X-Accel-Redirect.

Why X-Accel-Redirect?
  Large video files require HTTP Range requests for browser seeking to work.
  Nginx handles range requests natively and efficiently. FastAPI (uvicorn)
  would need to implement that logic itself and can't match Nginx performance
  for multi-GB files. X-Accel-Redirect lets FastAPI enforce authentication
  while delegating the actual byte-serving to Nginx.

How it works:
  1. Browser requests /api/media/video/{id}  (or /api/media/thumbnail/{id}).
  2. FastAPI checks the session cookie.
  3. FastAPI responds with an empty body and the header:
       X-Accel-Redirect: /internal/media/projects/{id}/video.mp4
  4. Nginx (which received the original request) sees that header, suppresses
     it from the downstream response, and serves the file from the path
     specified by its `internal` location block.

Nginx config (see nginx.conf):
  location /internal/media/ {
      internal;
      alias /var/lib/spotplayer/media/;
  }
"""

from pathlib import Path

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, RedirectResponse

from app.auth import is_authenticated
from app.config import settings
from app.database import get_db

router = APIRouter()


def _x_accel_response(relative_path: str, content_type: str) -> Response:
    """
    Return a bare Response that instructs Nginx to serve the file at
    *relative_path* (relative to settings.media_root) via X-Accel-Redirect.
    """
    internal_url = f"{settings.nginx_internal_prefix}/{relative_path}"
    return Response(
        content=b"",
        headers={
            "X-Accel-Redirect": internal_url,
            "Content-Type": content_type,
        },
    )


@router.get("/api/media/video/{project_id}")
async def serve_video(
    project_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    Authenticate and delegate video streaming to Nginx.

    The <video> element in the viewer sets its src to this URL.
    Nginx handles range requests so seeking works correctly on large files.
    """
    if not is_authenticated(request):
        return Response(status_code=401)

    cursor = await db.execute(
        "SELECT video_path, processing_status FROM projects WHERE id = ?",
        (project_id,),
    )
    project = await cursor.fetchone()
    if project is None:
        return Response(status_code=404)

    if project["processing_status"] != "ready" or not project["video_path"]:
        return Response(status_code=404, content=b"Video not ready yet")

    # video_path is stored relative to media_root.
    return _x_accel_response(project["video_path"], "video/mp4")


@router.get("/api/media/thumbnail/{project_id}")
async def serve_thumbnail(
    project_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    Authenticate and delegate thumbnail serving to Nginx.

    Used by the home page project cards. Falls back gracefully if the
    thumbnail hasn't been generated yet.
    """
    if not is_authenticated(request):
        return Response(status_code=401)

    cursor = await db.execute(
        "SELECT thumbnail_path FROM projects WHERE id = ?",
        (project_id,),
    )
    project = await cursor.fetchone()
    if project is None or not project["thumbnail_path"]:
        # Return a 204 (No Content) so the browser doesn't show a broken image.
        return Response(status_code=204)

    return _x_accel_response(project["thumbnail_path"], "image/jpeg")
