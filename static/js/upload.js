/**
 * upload.js — chunked video upload client
 *
 * Flow:
 *  1. User submits the form.
 *  2. POST /api/upload/init  (title + SRT + chunk count) → {upload_id, project_id}
 *  3. Split video file into CHUNK_SIZE_BYTES slices.
 *  4. Upload each slice sequentially to POST /api/upload/chunk.
 *  5. POST /api/upload/finalize → server queues background processing.
 *  6. Show success message.
 *
 * Chunks are uploaded sequentially (not in parallel) to avoid saturating the
 * connection and to make progress reporting straightforward.
 */

"use strict";

// Maximum bytes per chunk — must stay under Cloudflare's 100 MB request limit.
const CHUNK_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB

// --- DOM references (set up after DOMContentLoaded) -----------------------
let form, uploadBtn, errorBox, progressSection, progressLabel, progressBar,
    progressDetail, successSection;

document.addEventListener("DOMContentLoaded", () => {
  form            = document.getElementById("upload-form");
  uploadBtn       = document.getElementById("upload-btn");
  errorBox        = document.getElementById("upload-error");
  progressSection = document.getElementById("upload-progress");
  progressLabel   = document.getElementById("progress-label");
  progressBar     = document.getElementById("progress-bar");
  progressDetail  = document.getElementById("progress-detail");
  successSection  = document.getElementById("upload-success");

  form.addEventListener("submit", handleSubmit);
});

// ---------------------------------------------------------------------------

function showError(message) {
  errorBox.textContent = message;
  errorBox.style.display = "block";
  uploadBtn.disabled = false;
  uploadBtn.textContent = "Start upload";
}

function setProgress(numerator, denominator, label) {
  const pct = denominator > 0 ? Math.round((numerator / denominator) * 100) : 0;
  progressBar.style.width = pct + "%";
  progressDetail.textContent = label || `${numerator} / ${denominator} chunks`;
  if (progressLabel && label) progressLabel.textContent = label;
}

/** Upload a single FormData payload and return the parsed JSON response. */
async function postForm(url, formData) {
  const res = await fetch(url, { method: "POST", body: formData });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(json.detail || `Server returned ${res.status}`);
  }
  return json;
}

// ---------------------------------------------------------------------------

async function handleSubmit(e) {
  e.preventDefault();

  errorBox.style.display = "none";
  uploadBtn.disabled = true;
  uploadBtn.textContent = "Uploading…";

  const title      = document.getElementById("project-title").value.trim();
  const srtFile    = document.getElementById("srt-file").files[0];
  const videoFile  = document.getElementById("video-file").files[0];

  // Basic client-side validation.
  if (!title)     return showError("Please enter a project title.");
  if (!srtFile)   return showError("Please select an SRT subtitle file.");
  if (!videoFile) return showError("Please select a video file.");

  // Calculate how many chunks we'll need.
  const totalChunks = Math.max(1, Math.ceil(videoFile.size / CHUNK_SIZE_BYTES));

  // Show progress UI.
  form.style.display = "none";
  progressSection.style.display = "block";
  progressLabel.textContent = "Initialising…";
  setProgress(0, totalChunks, "Initialising…");

  try {
    // --- Step 1: init -------------------------------------------------------
    const initForm = new FormData();
    initForm.append("title",          title);
    initForm.append("srt_file",       srtFile, srtFile.name);
    initForm.append("total_chunks",   String(totalChunks));
    initForm.append("video_filename", videoFile.name);

    const { upload_id: uploadId, project_id: projectId } =
      await postForm("/api/upload/init", initForm);

    // --- Step 2: upload chunks ---------------------------------------------
    progressLabel.textContent = "Uploading video…";

    for (let i = 0; i < totalChunks; i++) {
      const start  = i * CHUNK_SIZE_BYTES;
      const end    = Math.min(start + CHUNK_SIZE_BYTES, videoFile.size);
      const slice  = videoFile.slice(start, end);

      const chunkForm = new FormData();
      chunkForm.append("upload_id",    uploadId);
      chunkForm.append("chunk_index",  String(i));
      chunkForm.append("chunk",        slice, `chunk_${i}`);

      await postForm("/api/upload/chunk", chunkForm);
      setProgress(i + 1, totalChunks, `Uploading chunk ${i + 1} of ${totalChunks}…`);
    }

    // --- Step 3: finalize --------------------------------------------------
    progressLabel.textContent = "Finalising…";
    setProgress(totalChunks, totalChunks, "Finalising upload…");

    const finalForm = new FormData();
    finalForm.append("upload_id",      uploadId);
    finalForm.append("video_filename", videoFile.name);

    await postForm("/api/upload/finalize", finalForm);

    // --- Done --------------------------------------------------------------
    progressSection.style.display = "none";
    successSection.style.display  = "block";

  } catch (err) {
    // Re-show the form so the user can correct the issue.
    progressSection.style.display = "none";
    form.style.display = "block";
    showError(err.message || "An unexpected error occurred. Please try again.");
  }
}
