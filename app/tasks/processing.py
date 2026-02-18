"""
Background video processing tasks.

Entry points:
  process_video()   — full flow: assemble chunks → transcode → thumbnail.
                      Called from /api/upload/finalize.
  reprocess_video() — processing only (no assembly), for already-uploaded
                      projects. Called from /api/project/{id}/reprocess.

FFmpeg binary detection:
  shutil.which() is used at module load time to locate ffmpeg and ffprobe.
  This handles cases where PATH differs between interactive shells and the
  uvicorn process (e.g. when started via systemd or a non-login shell).
  Common install paths are tried as fallbacks.
"""

import asyncio
import logging
import shutil
from pathlib import Path

import aiosqlite
import ffmpeg

from app.config import settings, PROJECTS_DIR, UPLOADS_DIR

logger = logging.getLogger(__name__)


# ── Binary detection ──────────────────────────────────────────────────────────

def _find_bin(name: str) -> str:
    """
    Return the absolute path to *name* (ffmpeg or ffprobe).
    Tries shutil.which() first, then common installation prefixes.
    Raises RuntimeError if not found anywhere.
    """
    path = shutil.which(name)
    if path:
        return path
    candidates = [
        f"/usr/bin/{name}",
        f"/usr/local/bin/{name}",
        f"/opt/homebrew/bin/{name}",    # macOS Homebrew
        f"/snap/bin/{name}",
    ]
    for c in candidates:
        if Path(c).is_file():
            return c
    raise RuntimeError(
        f"'{name}' not found. Install FFmpeg: sudo apt install ffmpeg"
    )

# Resolved once at import time — fails fast if ffmpeg isn't installed.
try:
    FFMPEG_BIN  = _find_bin("ffmpeg")
    FFPROBE_BIN = _find_bin("ffprobe")
    logger.info("FFmpeg: %s  |  ffprobe: %s", FFMPEG_BIN, FFPROBE_BIN)
except RuntimeError as _e:
    logger.warning("FFmpeg not found at startup: %s", _e)
    FFMPEG_BIN  = "ffmpeg"   # let it fail at runtime with a clear error
    FFPROBE_BIN = "ffprobe"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _is_h264_mp4(video_path: Path) -> bool:
    """Return True if *video_path* is H.264 in an MP4 container."""
    try:
        probe = ffmpeg.probe(str(video_path), cmd=FFPROBE_BIN)
        fmt = probe.get("format", {}).get("format_name", "")
        video_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
        if not video_streams:
            return False
        return video_streams[0].get("codec_name") == "h264" and "mp4" in fmt
    except Exception:
        return False


def _transcode_to_h264(input_path: Path, output_path: Path) -> None:
    """Re-encode *input_path* to H.264 + AAC MP4 at *output_path*."""
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
        .run(cmd=FFMPEG_BIN, quiet=True)
    )


def _extract_thumbnail(video_path: Path, thumbnail_path: Path, seek: int = 10) -> None:
    """Write a JPEG thumbnail from *video_path* at *seek* seconds."""
    try:
        (
            ffmpeg
            .input(str(video_path), ss=seek)
            .output(str(thumbnail_path), vframes=1, format="image2", vcodec="mjpeg")
            .overwrite_output()
            .run(cmd=FFMPEG_BIN, quiet=True)
        )
    except ffmpeg.Error:
        # Video shorter than seek point — grab the first frame instead.
        (
            ffmpeg
            .input(str(video_path))
            .output(str(thumbnail_path), vframes=1, format="image2", vcodec="mjpeg")
            .overwrite_output()
            .run(cmd=FFMPEG_BIN, quiet=True)
        )


def _assemble_chunks(chunk_dir: Path, output_path: Path, total_chunks: int) -> None:
    """Concatenate ordered chunk files into *output_path* using a 4 MB buffer."""
    BLOCK = 4 * 1024 * 1024
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
    """Update processing_status (and optional extra columns) in the DB."""
    fields = {"processing_status": status, **extra_fields}
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [project_id]
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(f"UPDATE projects SET {set_clause} WHERE id = ?", values)
        await db.commit()


def _find_source_file(project_dir: Path) -> Path:
    """
    Return the assembled source video file in *project_dir*.
    Looks for source.* first, then video.mp4 (already-processed).
    Raises FileNotFoundError if nothing suitable exists.
    """
    # Prefer source.* (pre-transcode) over video.mp4 (post-transcode).
    for pattern in ("source.*", "video.mp4"):
        matches = [p for p in project_dir.glob(pattern) if p.suffix != ".jpg"]
        if matches:
            return matches[0]
    raise FileNotFoundError(
        f"No source video found in {project_dir}. "
        "The file may have been deleted — please re-upload."
    )


# ── Core processing logic ─────────────────────────────────────────────────────

async def _run_processing(project_id: int, raw_path: Path) -> None:
    """
    Transcode (if needed) and extract thumbnail for *raw_path*.
    Updates the DB on success or failure.

    Called by both process_video() and reprocess_video().
    *raw_path* must already exist (chunks assembled or source file present).
    """
    project_dir = PROJECTS_DIR / str(project_id)
    final_path  = project_dir / "video.mp4"
    thumb_path  = project_dir / "thumbnail.jpg"
    loop = asyncio.get_event_loop()

    try:
        await _update_status(project_id, "processing")

        # --- Transcode if needed ------------------------------------------
        needs_transcode = not _is_h264_mp4(raw_path)
        if needs_transcode:
            logger.info("project %d: transcoding %s → H.264…", project_id, raw_path.name)
            await loop.run_in_executor(None, _transcode_to_h264, raw_path, final_path)
            # Remove the source to save space only if it differs from final.
            if raw_path != final_path:
                raw_path.unlink(missing_ok=True)
        else:
            # Already H.264/MP4 — rename to canonical name if necessary.
            if raw_path != final_path:
                raw_path.rename(final_path)
        logger.info("project %d: video ready at %s", project_id, final_path)

        # --- Thumbnail -------------------------------------------------------
        logger.info("project %d: extracting thumbnail", project_id)
        await loop.run_in_executor(None, _extract_thumbnail, final_path, thumb_path)

        # --- Update DB -------------------------------------------------------
        video_rel = str(final_path.relative_to(settings.media_root))
        thumb_rel = str(thumb_path.relative_to(settings.media_root))
        await _update_status(project_id, "ready", video_path=video_rel, thumbnail_path=thumb_rel)
        logger.info("project %d: processing complete", project_id)

    except Exception as exc:
        logger.exception("project %d: processing failed: %s", project_id, exc)
        await _update_status(project_id, "error")


# ── Public entry points ───────────────────────────────────────────────────────

async def process_video(
    project_id: int,
    session_id: str,
    total_chunks: int,
    original_filename: str,
) -> None:
    """
    Full pipeline: assemble chunks → process → clean up temp dir.
    Called as a FastAPI BackgroundTask from /api/upload/finalize.
    """
    chunk_dir = UPLOADS_DIR / session_id
    project_dir = PROJECTS_DIR / str(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)

    suffix   = Path(original_filename).suffix.lower() or ".mp4"
    raw_path = project_dir / f"source{suffix}"

    try:
        logger.info("project %d: assembling %d chunks", project_id, total_chunks)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _assemble_chunks, chunk_dir, raw_path, total_chunks)
        logger.info("project %d: assembly complete → %s", project_id, raw_path)

        await _run_processing(project_id, raw_path)

    except Exception as exc:
        logger.exception("project %d: assembly/processing failed: %s", project_id, exc)
        await _update_status(project_id, "error")

    finally:
        # Always remove the temp chunk directory.
        if chunk_dir.exists():
            shutil.rmtree(chunk_dir, ignore_errors=True)


async def reprocess_video(project_id: int) -> None:
    """
    Re-run processing on an already-assembled source file.
    Called from /api/project/{id}/reprocess when status is 'error'.
    Does NOT require chunks — works from whatever is in the project directory.
    """
    project_dir = PROJECTS_DIR / str(project_id)

    try:
        raw_path = _find_source_file(project_dir)
        logger.info("project %d: reprocessing from %s", project_id, raw_path)
        await _run_processing(project_id, raw_path)
    except FileNotFoundError as exc:
        logger.error("project %d: cannot reprocess — %s", project_id, exc)
        await _update_status(project_id, "error")
    except Exception as exc:
        logger.exception("project %d: reprocess failed: %s", project_id, exc)
        await _update_status(project_id, "error")
