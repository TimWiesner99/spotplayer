/**
 * upload.js — chunked video upload client
 *
 * The form's submit event is intercepted here. Files are split into 50 MB
 * chunks and POSTed one at a time to /api/upload/chunk.
 *
 * Bug-prevention notes:
 *   - This script is loaded at the bottom of <body> (no defer/DOMContentLoaded
 *     needed — the DOM is already fully parsed by the time this runs).
 *   - The <form> has method="post" so that if JS fails the browser sends a
 *     POST (giving a visible server error) instead of a silent GET.
 */

"use strict";

const CHUNK_BYTES = 50 * 1024 * 1024;  // 50 MB per chunk

// DOM refs — elements guaranteed present on this page.
const form            = document.getElementById("upload-form");
const uploadBtn       = document.getElementById("upload-btn");
const errorBox        = document.getElementById("upload-error");
const progressSection = document.getElementById("upload-progress");
const progressMsg     = document.getElementById("progress-message");
const progressFill    = document.getElementById("progress-fill");
const progressLeft    = document.getElementById("progress-left");
const progressRight   = document.getElementById("progress-right");
const successSection  = document.getElementById("upload-success");

// Step indicator dots.
const steps = {
  init:     document.getElementById("step-init"),
  upload:   document.getElementById("step-upload"),
  finalize: document.getElementById("step-finalize"),
};

// ── Helpers ──────────────────────────────────────────────────────────────────

function setStep(name) {
  const order = ["init", "upload", "finalize"];
  const idx = order.indexOf(name);
  order.forEach((s, i) => {
    const el = steps[s];
    if (!el) return;
    el.classList.toggle("active", i === idx);
    el.classList.toggle("done",   i < idx);
  });
}

function setProgress(pct, message, left, right) {
  progressFill.style.width = Math.min(100, pct) + "%";
  if (message)      progressMsg.textContent   = message;
  if (left != null) progressLeft.textContent  = left;
  if (right != null) progressRight.textContent = right;
}

function showError(msg) {
  errorBox.textContent    = msg;
  errorBox.style.display  = "block";
  uploadBtn.disabled      = false;
  uploadBtn.textContent   = "Start upload";
  form.style.display      = "block";
  progressSection.style.display = "none";
}

function formatBytes(b) {
  if (b < 1024)       return b + " B";
  if (b < 1048576)    return (b / 1024).toFixed(1) + " KB";
  return (b / 1048576).toFixed(1) + " MB";
}

/** POST a FormData payload and return parsed JSON; throws on HTTP error. */
async function postForm(url, data) {
  const res = await fetch(url, { method: "POST", body: data });
  let json;
  try   { json = await res.json(); }
  catch { json = {}; }
  if (!res.ok) {
    throw new Error(json.detail || `Server error ${res.status}`);
  }
  return json;
}

// ── Main upload handler ───────────────────────────────────────────────────────

async function handleSubmit(e) {
  e.preventDefault();

  const title     = document.getElementById("project-title").value.trim();
  const srtFile   = document.getElementById("srt-file").files[0];
  const videoFile = document.getElementById("video-file").files[0];

  errorBox.style.display = "none";

  if (!title)     return showError("Please enter a project title.");
  if (!srtFile)   return showError("Please select an SRT subtitle file.");
  if (!videoFile) return showError("Please select a video file.");

  uploadBtn.disabled    = true;
  uploadBtn.textContent = "Uploading…";
  form.style.display    = "none";
  progressSection.style.display = "block";

  const totalChunks = Math.max(1, Math.ceil(videoFile.size / CHUNK_BYTES));
  const totalSize   = videoFile.size;

  try {
    // ── Step 1: init ───────────────────────────────────────────────────────
    setStep("init");
    setProgress(2, "Initialising upload…", "Sending metadata + subtitle file", "");

    const initData = new FormData();
    initData.append("title",          title);
    initData.append("srt_file",       srtFile, srtFile.name);
    initData.append("total_chunks",   String(totalChunks));
    initData.append("video_filename", videoFile.name);

    const { upload_id: uploadId } = await postForm("/api/upload/init", initData);

    // ── Step 2: upload chunks ──────────────────────────────────────────────
    setStep("upload");
    let uploadedBytes = 0;
    const startTime   = Date.now();

    for (let i = 0; i < totalChunks; i++) {
      const start = i * CHUNK_BYTES;
      const end   = Math.min(start + CHUNK_BYTES, totalSize);
      const slice = videoFile.slice(start, end);

      const fd = new FormData();
      fd.append("upload_id",   uploadId);
      fd.append("chunk_index", String(i));
      fd.append("chunk",       slice, `chunk_${i}`);

      await postForm("/api/upload/chunk", fd);

      uploadedBytes += (end - start);
      const pct      = (uploadedBytes / totalSize) * 100;
      const elapsed  = (Date.now() - startTime) / 1000;
      const speed    = elapsed > 0 ? uploadedBytes / elapsed : 0;
      const remaining = speed > 0 ? ((totalSize - uploadedBytes) / speed) : 0;

      setProgress(
        pct,
        `Uploading chunk ${i + 1} of ${totalChunks}…`,
        `${formatBytes(uploadedBytes)} of ${formatBytes(totalSize)}`,
        remaining > 1 ? `~${Math.ceil(remaining)}s remaining` : ""
      );
    }

    // ── Step 3: finalize ───────────────────────────────────────────────────
    setStep("finalize");
    setProgress(98, "Finalising — assembling chunks on server…", "", "");

    const finalData = new FormData();
    finalData.append("upload_id",      uploadId);
    finalData.append("video_filename", videoFile.name);

    await postForm("/api/upload/finalize", finalData);

    setProgress(100, "Upload complete!", "Video processing started in background", "");

    // Brief pause so the user sees 100%, then show the success banner.
    await new Promise(r => setTimeout(r, 600));
    progressSection.style.display = "none";
    successSection.style.display  = "block";

  } catch (err) {
    showError(err.message || "An unexpected error occurred. Please try again.");
  }
}

// Attach listener directly — no DOMContentLoaded wrapper needed because this
// script is included at the bottom of <body>.
if (form) {
  form.addEventListener("submit", handleSubmit);
} else {
  console.error("SpotPlayer: #upload-form not found — upload.js loaded on wrong page?");
}
