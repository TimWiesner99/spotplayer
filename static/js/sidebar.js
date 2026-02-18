/**
 * sidebar.js — populate the sidebar project list via API
 *
 * Fetches /api/projects and renders compact project entries.
 * Marks the current project as active if on a viewer page.
 * Polls for status changes on any projects that are still processing.
 */

"use strict";

(function () {
  const list = document.getElementById("sidebar-project-list");
  if (!list) return;

  // Current project ID (set on viewer pages via data attribute on <body>).
  const currentProjectId = document.body.dataset.projectId
    ? parseInt(document.body.dataset.projectId, 10)
    : null;

  let pendingIds = [];   // project IDs still processing — polled periodically

  // Format YYYY-MM-DD as a short readable date.
  function shortDate(ts) {
    return ts ? ts.slice(0, 10) : "";
  }

  function renderThumb(project) {
    if (project.processing_status === "ready" && project.thumbnail_path) {
      return `<img class="sidebar-project-thumb"
                   src="/api/media/thumbnail/${project.id}"
                   alt="" loading="lazy"
                   onerror="this.style.display='none'">`;
    }
    const spinnerHtml = (project.processing_status === "processing" || project.processing_status === "pending")
      ? `<div class="mini-spinner"></div>` : `<span style="font-size:10px;color:var(--text-muted)">!</span>`;
    return `<div class="sidebar-project-thumb-placeholder">${spinnerHtml}</div>`;
  }

  function renderProjects(projects) {
    if (!projects.length) {
      list.innerHTML = `<p class="sidebar-empty">No projects yet.<br>Upload one to get started.</p>`;
      return;
    }

    pendingIds = projects
      .filter(p => p.processing_status === "pending" || p.processing_status === "processing")
      .map(p => p.id);

    list.innerHTML = projects.map(p => {
      const isActive = p.id === currentProjectId;
      const href = p.processing_status === "ready" ? `/project/${p.id}` : "#";
      return `
        <a href="${href}"
           class="sidebar-project${isActive ? " active" : ""}"
           title="${p.title}">
          ${renderThumb(p)}
          <div class="sidebar-project-info">
            <div class="sidebar-project-title">${p.title}</div>
            <div class="sidebar-project-date">${shortDate(p.upload_timestamp)}</div>
          </div>
        </a>`;
    }).join("");
  }

  async function load() {
    try {
      const res = await fetch("/api/projects");
      if (!res.ok) return;
      const projects = await res.json();
      renderProjects(projects);
    } catch (_) { /* network error — silently ignore */ }
  }

  // Poll processing projects every 4 s and reload sidebar when one finishes.
  async function pollPending() {
    if (!pendingIds.length) return;
    for (const id of pendingIds) {
      try {
        const res = await fetch(`/api/project/${id}/status`);
        if (!res.ok) continue;
        const { status } = await res.json();
        if (status === "ready" || status === "error") {
          await load();   // refresh entire sidebar when status changes
          return;
        }
      } catch (_) {}
    }
  }

  load();
  setInterval(pollPending, 4000);
})();
