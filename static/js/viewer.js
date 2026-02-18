/**
 * viewer.js — video player + transcript synchronisation
 *
 * Responsibilities:
 *  - Listen to the video's `timeupdate` event (~4 Hz).
 *  - Find the cue whose [start_time, end_time) window contains currentTime.
 *  - Apply the `.active` CSS class to that cue's <span> and remove it from
 *    the previously active span.
 *  - Scroll the transcript panel so the active cue stays in view.
 *  - Allow clicking any cue <span> to seek the video to that cue's start time.
 *
 * Performance notes:
 *  - Cues are collected once on load and sorted by start_time.
 *  - A binary search finds the candidate cue in O(log n) per timeupdate event.
 *    For a 2-hour interview this is ~500 cues → ~9 comparisons, negligible.
 *  - DOM writes only happen when the active cue actually changes.
 */

"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const video  = document.getElementById("video-player");
  const panel  = document.getElementById("transcript-panel");

  if (!video || !panel) return; // viewer elements absent (e.g., still processing)

  // --- Collect all cue spans -----------------------------------------------
  // Each span has data-start and data-end attributes (seconds as floats).
  const spans = Array.from(panel.querySelectorAll(".cue"));
  if (spans.length === 0) return;

  // Build a parallel array of {start, end, el} for fast lookup.
  const cues = spans.map(el => ({
    start: parseFloat(el.dataset.start),
    end:   parseFloat(el.dataset.end),
    el,
  }));

  // Cues should already be sorted by start_time (the server orders them),
  // but sort defensively in case the template renders them out of order.
  cues.sort((a, b) => a.start - b.start);

  // --- State ----------------------------------------------------------------
  let activeCue = null; // the currently highlighted {start, end, el} object

  // --- Binary search --------------------------------------------------------
  /**
   * Find the index of the last cue whose start_time <= t.
   * Returns -1 if t is before all cues.
   */
  function findCandidateIndex(t) {
    let lo = 0, hi = cues.length - 1, result = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >>> 1;
      if (cues[mid].start <= t) {
        result = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    return result;
  }

  /**
   * Return the cue object active at time t, or null if none.
   * A cue is active when start <= t < end.
   */
  function getCueAt(t) {
    const idx = findCandidateIndex(t);
    if (idx === -1) return null;
    const c = cues[idx];
    return (t < c.end) ? c : null;
  }

  // --- Highlight management -------------------------------------------------
  function setActiveCue(cue) {
    if (cue === activeCue) return; // no change — skip DOM update

    // Remove highlight from previous cue.
    if (activeCue) activeCue.el.classList.remove("active");

    activeCue = cue;

    if (activeCue) {
      activeCue.el.classList.add("active");
      // Scroll the active span into view, centred in the panel.
      activeCue.el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  // --- timeupdate handler ---------------------------------------------------
  video.addEventListener("timeupdate", () => {
    setActiveCue(getCueAt(video.currentTime));
  });

  // Also sync on seek completion (seeked fires after the user drags the scrubber).
  video.addEventListener("seeked", () => {
    setActiveCue(getCueAt(video.currentTime));
  });

  // --- Click-to-seek --------------------------------------------------------
  spans.forEach(el => {
    el.addEventListener("click", () => {
      const start = parseFloat(el.dataset.start);
      if (!isNaN(start)) {
        video.currentTime = start;
        // If the video is paused, start playing.
        if (video.paused) video.play().catch(() => {});
      }
    });
  });
});
