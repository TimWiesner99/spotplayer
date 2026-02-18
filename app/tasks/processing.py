"""
Background video processing tasks.

After all upload chunks have been assembled, `process_video` is queued as a
FastAPI BackgroundTask. It:
  1. Concatenates ordered chunk files into a single source video.
  2. Uses ffprobe to determine whether the source is already H.264/MP4.
  3. If transcoding is needed, re-encodes to H.264 + AAC in an MP4 container.
  4. Extracts a JPEG thumbnail at the 10-second mark.
  5. Updates the project's processing_status and file paths in the DB.
  6. Cleans up the temporary chunk directory.

Limitations (acceptable for this use-case, document for future work):
  - FastAPI BackgroundTasks are in-process; a server restart loses queued jobs.
    For production, replace with Celery + Redis or ARQ.
  - Progress within FFmpeg is not exposed to the frontend; only the coarse
    status (pending / processing / ready / error) is visible.
"""

import asyncio
import logging
import os
import shutil
from pathlib import Path

import aiosqlite
import ffmpeg

from app.config import settings, PROJECTS_DIR, UPLOADS_DIR

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_h264_mp4(video_path: Path) -> bool:
    """
    Return True if *video_path* is already an H.264 video in an MP4 container.
    Uses ffprobe (bundled with ffmpeg-python).
    """
    try:
        probe = ffmpeg.probe(str(video_path))
        fmt = probe.get("format", {}).get("format_name", "")
        video_streams = [
            s for s in probe.get("streams", []) if s.get("codec_type") == "video"
        ]
        if not video_streams:
            return False
        codec = video_streams[0].get("codec_name", "")
        return codec == "h264" and "mp4" in fmt
    except ffmpeg.Error:
        return False


def _transcode_to_h264(input_path: Path, output_path: Path) -> None:
    """
    Transcode *input_path* to H.264 + AAC MP4 at *output_path*.

    CRF 23 with 'medium' preset: good quality/size balance for interview footage.
    movflags faststart places the moov atom at the front so the browser can
    begin playback before the full file downloads.
    """
    (
        ffmpeg
        .input(str(input_path))
        .output(
            str(output_path),
            vcodec="libx264",
            acodec="aac",
            crf=23,
            preset="medium",
            movflags="faststart",
        )
        .overwrite_output()
        .run(quiet=True)
    )


def _extract_thumbnail(video_path: Path, thumbnail_path: Path, seek: int = 10) -> None:
    """
    Write a JPEG thumbnail from *video_path* at *seek* seconds to *thumbnail_path*.

    Falls back to frame 0 if the video is shorter than *seek* seconds.
    """
    try:
        (
            ffmpeg
            .input(str(video_path), ss=seek)
            .output(str(thumbnail_path), vframes=1, format="image2", vcodec="mjpeg")
            .overwrite_output()
            .run(quiet=True)
        )
    except ffmpeg.Error:
        # If seek point is beyond video length, grab the first frame.
        (
            ffmpeg
            .input(str(video_path))
            .output(str(thumbnail_path), vframes=1, format="image2", vcodec="mjpeg")
            .overwrite_output()
            .run(quiet=True)
        )


def _assemble_chunks(chunk_dir: Path, output_path: Path, total_chunks: int) -> None:
    """
    Concatenate chunk files (chunk_00000, chunk_00001, …) in order into *output_path*.

    Writes in 4 MB blocks to keep memory usage constant regardless of file size.
    """
    BLOCK = 4 * 1024 * 1024  # 4 MB read buffer
    with open(output_path, "wb") as out_f:
        for i in range(total_chunks):
            chunk_path = chunk_dir / f"chunk_{i:05d}"
            if not chunk_path.exists():
                raise FileNotFoundError(f"Missing chunk {i} at {chunk_path}")
            with open(chunk_path, "rb") as in_f:
                while True:
                    block = in_f.read(BLOCK)
                    if not block:
                        break
                    out_f.write(block)


async def _update_status(project_id: int, status: str, **extra_fields) -> None:
    """Update project processing_status (and optional extra columns) in the DB."""
    # Build SET clause dynamically for optional extra fields.
    fields = {"processing_status": status, **extra_fields}
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [project_id]

    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            f"UPDATE projects SET {set_clause} WHERE id = ?", values
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def process_video(
    project_id: int,
    session_id: str,
    total_chunks: int,
    original_filename: str,
) -> None:
    """
    Assemble chunks, transcode if needed, extract thumbnail, update DB.

    Called as a FastAPI BackgroundTask from the /api/upload/finalize endpoint.
    All FFmpeg work runs in a thread pool executor so it doesn't block the
    asyncio event loop.
    """
    chunk_dir = UPLOADS_DIR / session_id
    project_dir = PROJECTS_DIR / str(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(original_filename).suffix.lower() or ".mp4"
    raw_path = project_dir / f"source{suffix}"
    final_path = project_dir / "video.mp4"
    thumb_path = project_dir / "thumbnail.jpg"

    try:
        await _update_status(project_id, "processing")
        logger.info("project %d: assembling %d chunks", project_id, total_chunks)

        # --- 1. Assemble chunks (blocking I/O → run in executor) ----------
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, _assemble_chunks, chunk_dir, raw_path, total_chunks
        )
        logger.info("project %d: assembly complete → %s", project_id, raw_path)

        # --- 2. Transcode if not already H.264/MP4 -----------------------
        needs_transcode = not _is_h264_mp4(raw_path)
        if needs_transcode:
            logger.info("project %d: transcoding to H.264…", project_id)
            await loop.run_in_executor(
                None, _transcode_to_h264, raw_path, final_path
            )
            # Remove raw source to save disk space.
            raw_path.unlink(missing_ok=True)
        else:
            # Already H.264/MP4 — just rename to the canonical filename.
            raw_path.rename(final_path)
        logger.info("project %d: video ready at %s", project_id, final_path)

        # --- 3. Extract thumbnail ----------------------------------------
        logger.info("project %d: extracting thumbnail", project_id)
        await loop.run_in_executor(None, _extract_thumbnail, final_path, thumb_path)

        # --- 4. Update DB ---------------------------------------------------
        # Store paths relative to media_root so the DB is location-independent.
        video_rel = str(final_path.relative_to(settings.media_root))
        thumb_rel = str(thumb_path.relative_to(settings.media_root))
        await _update_status(
            project_id,
            "ready",
            video_path=video_rel,
            thumbnail_path=thumb_rel,
        )
        logger.info("project %d: processing complete", project_id)

    except Exception as exc:
        logger.exception("project %d: processing failed: %s", project_id, exc)
        await _update_status(project_id, "error")

    finally:
        # Always clean up the temporary chunk directory.
        if chunk_dir.exists():
            shutil.rmtree(chunk_dir, ignore_errors=True)
