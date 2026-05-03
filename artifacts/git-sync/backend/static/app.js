const API = "/v1";

let pollingInterval = null;

async function apiFetch(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function toast(msg, type = "info") {
  let container = document.querySelector(".toast-container");
  if (!container) {
    container = document.createElement("div");
    container.className = "toast-container";
    document.body.appendChild(container);
  }
  const el = document.createElement("div");
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

function formatRelative(isoStr) {
  if (!isoStr) return "Never";
  const diff = Math.floor((Date.now() - new Date(isoStr + "Z").getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function shortUrl(url) {
  return url.replace(/^https?:\/\//, "").replace(/\.git$/, "");
}

function statusBadge(status) {
  if (!status) return `<span class="status-badge status-never">Never Synced</span>`;
  const map = {
    success: "status-success",
    error: "status-error",
    running: "status-running",
  };
  return `<span class="status-badge ${map[status] || "status-never"}">${status}</span>`;
}

function renderCard(c) {
  const scheduleHtml = c.schedule
    ? `<span class="meta-item">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        ${c.schedule}
       </span>`
    : `<span class="meta-item" style="color:var(--text-subtle)">Manual only</span>`;

  const lastSyncHtml = `<span class="meta-item">
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.6"/></svg>
    ${formatRelative(c.last_sync)}
  </span>`;

  return `
  <div class="config-card" id="card-${c.id}">
    <div class="card-header">
      <span class="card-title">${escHtml(c.name)}</span>
      ${statusBadge(c.last_status)}
    </div>
    <div class="card-body">
      <div class="repo-flow">
        <div class="repo-row">
          <span class="repo-label">SRC</span>
          <span class="repo-url" title="${escHtml(c.source_url)}">${escHtml(shortUrl(c.source_url))}</span>
          <span class="repo-branch">${escHtml(c.source_branch)}</span>
        </div>
        <div class="repo-row flow-arrow">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg>
        </div>
        <div class="repo-row">
          <span class="repo-label">DST</span>
          <span class="repo-url" title="${escHtml(c.dest_url)}">${escHtml(shortUrl(c.dest_url))}</span>
          <span class="repo-branch">${escHtml(c.dest_branch)}</span>
        </div>
      </div>
      <div class="card-meta">
        ${scheduleHtml}
        ${lastSyncHtml}
      </div>
    </div>
    <div class="card-actions">
      <button class="btn btn-blue btn-sm" onclick="triggerSync(${c.id}, this)">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.6"/></svg>
        Sync Now
      </button>
      <button class="btn btn-ghost btn-sm" onclick="openLogs(${c.id}, '${escHtml(c.name)}')">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
        Logs
      </button>
      <button class="btn btn-ghost btn-sm" onclick="openEditModal(${c.id})">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
        Edit
      </button>
      <button class="btn btn-danger btn-sm" onclick="deleteConfig(${c.id}, '${escHtml(c.name)}')">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>
        Delete
      </button>
    </div>
  </div>`;
}

function escHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function loadConfigs() {
  try {
    const configs = await apiFetch("/configs");
    const grid = document.getElementById("configs-grid");
    const empty = document.getElementById("empty-state");
    if (configs.length === 0) {
      grid.innerHTML = "";
      empty.classList.remove("hidden");
    } else {
      empty.classList.add("hidden");
      grid.innerHTML = configs.map(renderCard).join("");
    }
  } catch (e) {
    toast("Failed to load configs: " + e.message, "error");
  }
}

async function triggerSync(id, btn) {
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Syncing...`;
  try {
    await apiFetch(`/sync/${id}`, { method: "POST" });
    toast("Sync started in background", "success");
    setTimeout(loadConfigs, 1000);
    setTimeout(loadConfigs, 4000);
    setTimeout(loadConfigs, 8000);
  } catch (e) {
    toast("Sync failed: " + e.message, "error");
    btn.disabled = false;
    btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.6"/></svg> Sync Now`;
  }
}

async function deleteConfig(id, name) {
  if (!confirm(`Delete sync configuration "${name}"?\nThis will also delete all its logs.`)) return;
  try {
    await apiFetch(`/configs/${id}`, { method: "DELETE" });
    toast(`"${name}" deleted`, "info");
    loadConfigs();
  } catch (e) {
    toast("Delete failed: " + e.message, "error");
  }
}

// Modal - Add/Edit
function openAddModal() {
  document.getElementById("modal-title").textContent = "Add Sync Configuration";
  document.getElementById("form-submit-btn").textContent = "Create";
  document.getElementById("edit-id").value = "";
  document.getElementById("config-form").reset();
  document.getElementById("f-source-branch").value = "main";
  document.getElementById("f-dest-branch").value = "main";
  document.getElementById("modal-overlay").classList.remove("hidden");
}

async function openEditModal(id) {
  try {
    const configs = await apiFetch("/configs");
    const c = configs.find((x) => x.id === id);
    if (!c) return;
    document.getElementById("modal-title").textContent = "Edit Sync Configuration";
    document.getElementById("form-submit-btn").textContent = "Save";
    document.getElementById("edit-id").value = id;
    document.getElementById("f-name").value = c.name;
    document.getElementById("f-source-url").value = c.source_url;
    document.getElementById("f-source-branch").value = c.source_branch;
    document.getElementById("f-dest-url").value = c.dest_url;
    document.getElementById("f-dest-branch").value = c.dest_branch;
    document.getElementById("f-schedule").value = c.schedule || "";
    document.getElementById("modal-overlay").classList.remove("hidden");
  } catch (e) {
    toast("Failed to load config: " + e.message, "error");
  }
}

function closeModal() {
  document.getElementById("modal-overlay").classList.add("hidden");
}

function closeModalIfOutside(e) {
  if (e.target === document.getElementById("modal-overlay")) closeModal();
}

function setCron(val) {
  document.getElementById("f-schedule").value = val;
}

async function submitConfigForm(e) {
  e.preventDefault();
  const btn = document.getElementById("form-submit-btn");
  const editId = document.getElementById("edit-id").value;
  const body = {
    name: document.getElementById("f-name").value.trim(),
    source_url: document.getElementById("f-source-url").value.trim(),
    source_branch: document.getElementById("f-source-branch").value.trim() || "main",
    dest_url: document.getElementById("f-dest-url").value.trim(),
    dest_branch: document.getElementById("f-dest-branch").value.trim() || "main",
    schedule: document.getElementById("f-schedule").value.trim() || null,
  };
  btn.disabled = true;
  btn.textContent = "Saving...";
  try {
    if (editId) {
      await apiFetch(`/configs/${editId}`, { method: "PUT", body: JSON.stringify(body) });
      toast("Configuration updated", "success");
    } else {
      await apiFetch("/configs", { method: "POST", body: JSON.stringify(body) });
      toast("Sync configuration created", "success");
    }
    closeModal();
    loadConfigs();
  } catch (e) {
    toast("Error: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = editId ? "Save" : "Create";
  }
}

// Logs modal
async function openLogs(configId, name) {
  document.getElementById("logs-title").textContent = `Logs — ${name}`;
  document.getElementById("logs-overlay").classList.remove("hidden");
  document.getElementById("logs-list").innerHTML = `<div class="logs-empty">Loading…</div>`;
  try {
    const logs = await apiFetch(`/logs?config_id=${configId}`);
    renderLogs(logs);
  } catch (e) {
    document.getElementById("logs-list").innerHTML = `<div class="logs-empty">Failed to load: ${e.message}</div>`;
  }
}

function renderLogs(logs) {
  const list = document.getElementById("logs-list");
  if (!logs.length) {
    list.innerHTML = `<div class="logs-empty">No logs yet for this configuration.</div>`;
    return;
  }
  list.innerHTML = logs.map((l) => {
    const statusClass = l.status === "success" ? "status-success" : l.status === "error" ? "status-error" : "status-running";
    const duration = l.finished_at
      ? `${((new Date(l.finished_at + "Z") - new Date(l.started_at + "Z")) / 1000).toFixed(1)}s`
      : "running…";
    return `
    <div class="log-entry">
      <div class="log-entry-header" onclick="toggleLog(this)">
        <div class="log-entry-meta">
          <span class="status-badge ${statusClass}">${l.status}</span>
          <span>${new Date(l.started_at + "Z").toLocaleString()}</span>
          <span>${duration}</span>
        </div>
        <svg class="log-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
      </div>
      <pre class="log-output">${escHtml(l.output || "(no output)")}</pre>
    </div>`;
  }).join("");
}

function toggleLog(header) {
  const output = header.nextElementSibling;
  const chevron = header.querySelector(".log-chevron");
  output.classList.toggle("expanded");
  chevron.classList.toggle("open");
}

function closeLogs() {
  document.getElementById("logs-overlay").classList.add("hidden");
}

function closeLogsIfOutside(e) {
  if (e.target === document.getElementById("logs-overlay")) closeLogs();
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeModal();
    closeLogs();
  }
});

// Auto refresh
setInterval(loadConfigs, 10000);
loadConfigs();
