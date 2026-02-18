"""
Chunked upload endpoints.

Flow:
  1. POST /upload/init        — client sends project title + SRT file + chunk count.
                                Server creates project, parses SRT, returns upload_id.
  2. POST /upload/chunk       — client sends one chunk at a time (≤ 50 MB each).
                                Server writes chunk_{index} to a temp directory.
  3. POST /upload/finalize    — client signals all chunks are uploaded.
                                Server kicks off background processing.

  GET  /upload                — render the upload form page.

Why chunked?
  Cloudflare's free plan has a 100 MB per-request body limit. Splitting the
  video into 50 MB chunks keeps every individual request well under that cap.
"""

import uuid
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import is_authenticated, require_api_auth
from app.config import UPLOADS_DIR, PROJECTS_DIR
from app.database import get_db
from app.srt_parser import parse_srt
from app.tasks.processing import process_video

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Maximum bytes accepted per chunk upload (50 MB).
MAX_CHUNK_BYTES = 50 * 1024 * 1024


# ---------------------------------------------------------------------------
# Upload form page
# ---------------------------------------------------------------------------

@router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    """Render the upload form. Requires authentication."""
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse("upload.html", {"request": request})


# ---------------------------------------------------------------------------
# Step 1: initialise a new upload session
# ---------------------------------------------------------------------------

@router.post("/api/upload/init")
async def upload_init(
    request: Request,
    title: str = Form(...),
    total_chunks: int = Form(...),
    video_filename: str = Form(...),
    srt_file: UploadFile = File(...),
    db: aiosqlite.Connection = Depends(get_db),
    _: None = Depends(require_api_auth),
):
    """
    Create a project, parse the SRT, and open an upload session.

    The SRT is small enough to upload in one shot here. The video is uploaded
    chunk-by-chunk in subsequent calls to /api/upload/chunk.

    Returns:
        {"upload_id": "<uuid>", "project_id": <int>}
    """
    if total_chunks < 1:
        raise HTTPException(status_code=400, detail="total_chunks must be ≥ 1")

    # --- Parse SRT ----------------------------------------------------------
    try:
        srt_bytes = await srt_file.read()
        srt_text = srt_bytes.decode("utf-8", errors="replace")
        cues = parse_srt(srt_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid SRT file: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read SRT file: {exc}")

    # --- Create project record ---------------------------------------------
    cursor = await db.execute(
        "INSERT INTO projects (title, processing_status) VALUES (?, 'pending')",
        (title.strip(),),
    )
    await db.commit()
    project_id = cursor.lastrowid

    # --- Save SRT file to disk --------------------------------------------
    project_dir = PROJECTS_DIR / str(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)
    srt_path = project_dir / "subtitles.srt"
    srt_path.write_bytes(srt_bytes)

    # Store relative path.
    from app.config import settings
    srt_rel = str(srt_path.relative_to(settings.media_root))
    await db.execute(
        "UPDATE projects SET srt_path = ? WHERE id = ?",
        (srt_rel, project_id),
    )

    # --- Bulk-insert cues --------------------------------------------------
    await db.executemany(
        "INSERT INTO cues (project_id, index_num, start_time, end_time, text) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (project_id, c["index_num"], c["start_time"], c["end_time"], c["text"])
            for c in cues
        ],
    )
    await db.commit()

    # --- Create upload session --------------------------------------------
    session_id = str(uuid.uuid4())
    chunk_dir = UPLOADS_DIR / session_id
    chunk_dir.mkdir(parents=True, exist_ok=True)

    await db.execute(
        "INSERT INTO upload_sessions (id, project_id, total_chunks) VALUES (?, ?, ?)",
        (session_id, project_id, total_chunks),
    )
    await db.commit()

    return JSONResponse({"upload_id": session_id, "project_id": project_id})


# ---------------------------------------------------------------------------
# Step 2: upload a single chunk
# ---------------------------------------------------------------------------

@router.post("/api/upload/chunk")
async def upload_chunk(
    request: Request,
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    chunk: UploadFile = File(...),
    db: aiosqlite.Connection = Depends(get_db),
    _: None = Depends(require_api_auth),
):
    """
    Receive and persist a single video chunk.

    Chunks are written to:  UPLOADS_DIR / {upload_id} / chunk_{index:05d}

    The client is responsible for uploading chunks in any order; the server
    assembles them in index order during finalization.

    Returns:
        {"received": <chunk_index>}
    """
    # Validate session exists.
    cursor = await db.execute(
        "SELECT total_chunks FROM upload_sessions WHERE id = ?",
        (upload_id,),
    )
    session = await cursor.fetchone()
    if session is None:
        raise HTTPException(status_code=404, detail="Upload session not found")

    total_chunks = session["total_chunks"]
    if chunk_index < 0 or chunk_index >= total_chunks:
        raise HTTPException(
            status_code=400,
            detail=f"chunk_index {chunk_index} out of range [0, {total_chunks})",
        )

    # Read and size-check chunk data before writing.
    chunk_data = await chunk.read(MAX_CHUNK_BYTES + 1)
    if len(chunk_data) > MAX_CHUNK_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Chunk exceeds maximum size of {MAX_CHUNK_BYTES // (1024*1024)} MB",
        )

    # Write chunk to temp directory.
    chunk_path = UPLOADS_DIR / upload_id / f"chunk_{chunk_index:05d}"
    chunk_path.write_bytes(chunk_data)

    return JSONResponse({"received": chunk_index})


# ---------------------------------------------------------------------------
# Step 3: finalise the upload and start background processing
# ---------------------------------------------------------------------------

@router.post("/api/upload/finalize")
async def upload_finalize(
    request: Request,
    upload_id: str = Form(...),
    video_filename: str = Form(...),
    background_tasks: BackgroundTasks = None,
    db: aiosqlite.Connection = Depends(get_db),
    _: None = Depends(require_api_auth),
):
    """
    Verify all chunks arrived, then queue FFmpeg processing as a background task.

    Returns:
        {"project_id": <int>}
    """
    # Look up session.
    cursor = await db.execute(
        "SELECT project_id, total_chunks FROM upload_sessions WHERE id = ?",
        (upload_id,),
    )
    session = await cursor.fetchone()
    if session is None:
        raise HTTPException(status_code=404, detail="Upload session not found")

    project_id = session["project_id"]
    total_chunks = session["total_chunks"]

    # Verify all chunk files are present on disk.
    chunk_dir = UPLOADS_DIR / upload_id
    missing = [
        i
        for i in range(total_chunks)
        if not (chunk_dir / f"chunk_{i:05d}").exists()
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing chunks: {missing}. Re-upload the missing pieces.",
        )

    # Queue background processing (assembly + transcode + thumbnail).
    background_tasks.add_task(
        process_video,
        project_id=project_id,
        session_id=upload_id,
        total_chunks=total_chunks,
        original_filename=video_filename,
    )

    return JSONResponse({"project_id": project_id})
