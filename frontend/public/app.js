const API = "http://127.0.0.1:8000/api/v1";
const VALID_TABS = ["search", "ingest", "documents", "status"];

// ── Helpers ────────────────────────────────────────────────────────────────
function formatBytes(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1048576).toFixed(1) + " MB";
}

function formatRelativeTime(isoString) {
  if (!isoString) return "Never";
  const then = new Date(isoString.endsWith("Z") ? isoString : isoString + "Z");
  if (isNaN(then.getTime())) return isoString;
  const diffMs = Date.now() - then.getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return then.toLocaleDateString();
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function getExt(name) {
  return (name.split(".").pop() || "").toLowerCase();
}

function getFileIcon(name) {
  const ext = getExt(name);
  const map = {
    py: "#3572A5", js: "#f7df1e", ts: "#3178c6", java: "#b07219",
    c: "#555555", cpp: "#f34b7d", h: "#555555", go: "#00add8",
    rs: "#dea584", rb: "#cc342d", php: "#4f5d95", html: "#e34c26",
    css: "#563d7c", scss: "#c6538c", json: "#292929", yaml: "#cb171e",
    toml: "#9c4221", ini: "#6d8086", cfg: "#6d8086", conf: "#6d8086",
    sh: "#89e051", bat: "#c1f12e", ps1: "#012456", md: "#083fa1",
    rst: "#f5f5f5", sql: "#e38c00", pdf: "#ff3b00", docx: "#2b579a",
    xls: "#217346", xlsx: "#217346", csv: "#217346", tsv: "#217346",
    jsonl: "#292929", xml: "#f16529", zip: "#ffd33d", "7z": "#ffd33d",
    rar: "#ffd33d", txt: "#607d8b", log: "#607d8b", env: "#ecd53f",
  };
  const color = map[ext] || "#607d8b";
  return `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/></svg>`;
}

// Parses backend snippets shaped like "  L42: some code" or "> L43: matched line"
// (with bare "  ..." lines marking omitted context) into structured rows.
function parseSnippetLines(snippet) {
  const rawLines = snippet.split("\n");
  const rows = [];
  const lineRe = /^(> |  )L(\d+): ?(.*)$/;

  for (const raw of rawLines) {
    if (/^\s*\.\.\.\s*$/.test(raw)) {
      rows.push({ ellipsis: true });
      continue;
    }
    const m = raw.match(lineRe);
    if (m) {
      rows.push({ matched: m[1] === "> ", lineNo: m[2], code: m[3] });
    } else {
      rows.push({ matched: false, lineNo: "", code: raw });
    }
  }
  return rows;
}

function highlightTerms(text, query) {
  if (!query) return text;
  try {
    const regex = new RegExp(`(${query.trim().split(/\s+/).map(t => t.replace(/[-\/\\^$*+?.()|[\]{}]/g, "\\$&")).join("|")})`, "gi");
    return text.replace(regex, "<mark>$1</mark>");
  } catch (e) {
    return text;
  }
}

function renderSnippetBlock(snippet, query) {
  const rows = parseSnippetLines(snippet);
  const lines = rows.map((row) => {
    if (row.ellipsis) {
      return `<div class="snippet-row snippet-ellipsis"><span class="snippet-gutter"></span><span class="snippet-code">⋯</span></div>`;
    }
    const codeHtml = highlightTerms(escapeHtml(row.code), query);
    return `<div class="snippet-row${row.matched ? " snippet-row-matched" : ""}">` +
      `<span class="snippet-gutter">${row.lineNo}</span>` +
      `<span class="snippet-code">${codeHtml}</span>` +
      `</div>`;
  }).join("");
  return `<div class="result-snippet-code">${lines}</div>`;
}

function getExtBadgeClass(ext) {
  const map = {
    py: "badge-blue", js: "badge-yellow", ts: "badge-blue", java: "badge-red",
    c: "badge-gray", cpp: "badge-blue", go: "badge-blue", rs: "badge-orange",
    md: "badge-purple", json: "badge-yellow", yaml: "badge-red",
    zip: "badge-yellow", "7z": "badge-yellow",
  };
  return map[ext] || "badge-gray";
}

// ── Toast System ──────────────────────────────────────────────────────────
function showToast(title, message, type = "info", duration = 4000) {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  toast.className = "toast";

  const icons = {
    success: `<svg class="toast-icon success" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`,
    error: `<svg class="toast-icon error" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
    info: `<svg class="toast-icon info" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`,
  };

  toast.innerHTML = `
    ${icons[type] || icons.info}
    <div class="toast-body">
      <div class="toast-title">${escapeHtml(title)}</div>
      ${message ? `<div class="toast-message">${escapeHtml(message)}</div>` : ""}
    </div>
    <button class="toast-close" aria-label="Dismiss">&times;</button>
  `;

  container.appendChild(toast);

  const close = () => {
    toast.classList.add("removing");
    setTimeout(() => toast.remove(), 200);
  };

  toast.querySelector(".toast-close").addEventListener("click", close);
  if (duration > 0) setTimeout(close, duration);
  return toast;
}

// ── Status Helpers ────────────────────────────────────────────────────────
function showMsg(el, msg, ok) {
  el.className = "status-msg " + (ok ? "ok" : "err");
  el.textContent = msg;
}

function showLoading(el, msg) {
  el.className = "status-msg loading";
  el.innerHTML = `<div class="spinner"></div> ${msg}`;
}

// ── Progress Modal ────────────────────────────────────────────────────────
let activePollers = {};
const POLL_INTERVAL = 500;

function openModal(title) {
  document.getElementById("modal-title").textContent = title;
  document.getElementById("progress-modal").classList.add("active");
  document.getElementById("progress-fill").style.width = "0%";
  document.getElementById("progress-text").textContent = "Starting...";
  document.getElementById("progress-details").innerHTML = "";
}

function closeModal() {
  document.getElementById("progress-modal").classList.remove("active");
}

function updateProgress(data) {
  const pct = data.total_files > 0 ? Math.round((data.processed_files / data.total_files) * 100) : 0;
  document.getElementById("progress-fill").style.width = pct + "%";
  document.getElementById("progress-text").textContent =
    `${data.processed_files} / ${data.total_files} (${pct}%) — ${data.current_file || "..."}`;

  const details = document.getElementById("progress-details");
  const elapsed = data.elapsed_seconds.toFixed(1);

  if (data.status === "running" || data.status === "pending") {
    details.innerHTML = `<div class="progress-line current"><div class="spinner spinner-sm"></div> Processing... ${elapsed}s elapsed</div>`;
  } else if (data.status === "done") {
    const ok = data.result?.files?.filter((f) => f.status === "ok").length || 0;
    const err = data.result?.files?.filter((f) => f.status === "error").length || 0;
    const skip = data.result?.files?.filter((f) => f.status === "skipped").length || 0;
    details.innerHTML = `
      <div class="progress-line ok">✓ ${ok} ingested</div>
      ${skip ? `<div class="progress-line">⊘ ${skip} skipped</div>` : ""}
      ${err ? `<div class="progress-line err">✗ ${err} errors</div>` : ""}
      <div class="progress-summary">Done in ${elapsed}s — ${data.result?.total_vectors || 0} vectors created</div>
    `;
    document.getElementById("progress-text").textContent = "Complete!";
    document.getElementById("progress-fill").style.width = "100%";
  } else if (data.status === "error") {
    details.innerHTML = `<div class="progress-line err">✗ ${data.error || "Unknown error"}</div>`;
    document.getElementById("progress-text").textContent = "Error";
  }
}

function startPolling(jobId, label, onDone) {
  activeUploadCount++;
  updateUploadIndicator();
  openModal(label);
  const timer = setInterval(async () => {
    try {
      const res = await fetch(`${API}/jobs/${jobId}`);
      if (!res.ok) { clearInterval(timer); closeModal(); return; }
      const data = await res.json();
      updateProgress(data);
      if (data.status === "done" || data.status === "error") {
        clearInterval(timer);
        delete activePollers[jobId];
        activeUploadCount = Math.max(0, activeUploadCount - 1);
        updateUploadIndicator();
        if (onDone) onDone(data);
        setTimeout(() => {
          if (document.getElementById("progress-modal").classList.contains("active")) closeModal();
        }, 3000);
      }
    } catch {
      clearInterval(timer);
      delete activePollers[jobId];
      activeUploadCount = Math.max(0, activeUploadCount - 1);
      updateUploadIndicator();
      closeModal();
    }
  }, POLL_INTERVAL);
  activePollers[jobId] = timer;
}

document.getElementById("modal-close").addEventListener("click", closeModal);
document.getElementById("progress-modal").addEventListener("click", (e) => {
  if (e.target.id === "progress-modal") closeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

// ── Tabs / Routing ────────────────────────────────────────────────────────
function getTab() {
  const hash = location.hash.replace("#", "");
  return VALID_TABS.includes(hash) ? hash : "search";
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === name));
  history.replaceState(null, "", `#${name}`);
  if (name === "status") loadStatus();
  if (name === "documents") { extensionsLoaded = false; loadDocuments(); }
  // Close mobile menu after switching
  document.getElementById("main-nav").classList.remove("open");
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
});

window.addEventListener("hashchange", () => switchTab(getTab()));

// Mobile hamburger menu
document.getElementById("menu-toggle").addEventListener("click", () => {
  document.getElementById("main-nav").classList.toggle("open");
});

// ── Search ───────────────────────────────────────────────────────────────
let searchMode = "top";
let searchQuery = "";
let searchOffset = 0;
const TOP_SIZE = 10;
const ALL_PAGE_SIZE = 20;

document.querySelectorAll(".mode-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".mode-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    searchMode = btn.dataset.mode;
    if (searchQuery) {
      searchOffset = 0;
      runSearch();
    }
  });
});

const searchInput = document.getElementById("search-input");
const searchClear = document.getElementById("search-clear");

searchInput.addEventListener("input", () => {
  searchClear.classList.toggle("visible", searchInput.value.length > 0);
});

searchClear.addEventListener("click", () => {
  searchInput.value = "";
  searchClear.classList.remove("visible");
  searchInput.focus();
});

async function runSearch() {
  const resultsEl = document.getElementById("search-results");
  const metaEl = document.getElementById("search-meta");
  const loadingEl = document.getElementById("search-loading");
  const paginationEl = document.getElementById("search-pagination");
  const isTop = searchMode === "top";
  const size = isTop ? TOP_SIZE : ALL_PAGE_SIZE;
  const offset = isTop ? 0 : searchOffset;

  resultsEl.innerHTML = "";
  metaEl.textContent = "";
  paginationEl.style.display = "none";
  loadingEl.classList.add("active");

  const allChunks = isTop ? "" : "&all_chunks=true";
  try {
    const res = await fetch(`${API}/search?q=${encodeURIComponent(searchQuery)}&size=${size}&offset=${offset}&content_preview=true${allChunks}`);
    const data = await res.json();
    loadingEl.classList.remove("active");

    metaEl.textContent = isTop
      ? `Top ${data.results.length} result${data.results.length !== 1 ? "s" : ""} for "${data.query}"`
      : `${data.total} result${data.total !== 1 ? "s" : ""} for "${data.query}"`;

    if (data.results.length === 0) {
      resultsEl.innerHTML = `
        <div class="empty-state">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          <h3>No results found</h3>
          <p>Try adjusting your search terms or check the spelling.</p>
        </div>
      `;
      return;
    }

    if (!isTop && data.total > ALL_PAGE_SIZE) {
      paginationEl.style.display = "flex";
      document.getElementById("search-page-info").textContent =
        `Showing ${searchOffset + 1}-${Math.min(searchOffset + ALL_PAGE_SIZE, data.total)} of ${data.total}`;
      document.getElementById("search-prev").disabled = searchOffset === 0;
      document.getElementById("search-next").disabled = searchOffset + ALL_PAGE_SIZE >= data.total;
    }

    data.results.forEach((r, idx) => {
      const card = document.createElement("div");
      card.className = "result-card";
      card.dataset.index = idx;

      const ext = getExt(r.filepath);
      const snippetBlockHtml = renderSnippetBlock(r.snippet, searchQuery);

      card.innerHTML = `
        <div class="result-header">
          <div class="result-filepath-wrap">
            <span class="result-file-icon">${getFileIcon(r.filepath)}</span>
            <span class="result-filepath" title="${escapeHtml(r.filepath)}">${escapeHtml(r.filepath)}</span>
            ${ext ? `<span class="badge ${getExtBadgeClass(ext)} result-ext-badge">${ext}</span>` : ""}
          </div>
          <div class="result-header-actions">
            <span class="result-score" title="BM25 relevance score">${r.score.toFixed(3)}</span>
            <button class="btn btn-ghost btn-icon copy-btn" data-text="${escapeHtml(r.filepath)}" title="Copy path">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            </button>
          </div>
        </div>
        <div class="result-meta-row">
          <span>Line ${r.line_offset + 1}</span>
        </div>
        ${snippetBlockHtml}
      `;
      resultsEl.appendChild(card);
    });

    // Copy button handlers
    resultsEl.querySelectorAll(".copy-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(btn.dataset.text);
          showToast("Copied!", btn.dataset.text, "success", 2000);
        } catch (err) {
          showToast("Copy failed", err.message, "error");
        }
      });
    });
  } catch (err) {
    loadingEl.classList.remove("active");
    metaEl.textContent = "";
    paginationEl.style.display = "none";
    resultsEl.innerHTML = `
      <div class="empty-state">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
        <h3>Search error</h3>
        <p>${escapeHtml(err.message)}</p>
        <button class="btn btn-primary btn-sm mt-2" onclick="document.getElementById('search-form').dispatchEvent(new Event('submit'))">Retry</button>
      </div>
    `;
  }
}

document.getElementById("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = searchInput.value.trim();
  if (!q) return;
  searchQuery = q;
  searchOffset = 0;
  runSearch();
});

document.getElementById("search-prev").addEventListener("click", () => {
  searchOffset = Math.max(0, searchOffset - ALL_PAGE_SIZE);
  runSearch();
});

document.getElementById("search-next").addEventListener("click", () => {
  searchOffset += ALL_PAGE_SIZE;
  runSearch();
});

// Keyboard navigation for search results
document.addEventListener("keydown", (e) => {
  if (!document.getElementById("search").classList.contains("active")) return;
  const cards = document.querySelectorAll(".result-card");
  if (cards.length === 0) return;
  const active = document.querySelector(".result-card.selected");
  let idx = active ? Array.from(cards).indexOf(active) : -1;
  if (e.key === "ArrowDown") { e.preventDefault(); idx = Math.min(idx + 1, cards.length - 1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); idx = Math.max(idx - 1, 0); }
  else if (e.key === "Enter" && active) {
    e.preventDefault();
    const path = active.querySelector(".result-filepath").textContent;
    navigator.clipboard.writeText(path).then(() => showToast("Copied!", path, "success", 2000));
  } else { return; }
  cards.forEach((c) => c.classList.remove("selected"));
  if (cards[idx]) {
    cards[idx].classList.add("selected");
    cards[idx].scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
});

// ── Ingest: Unified Upload (Files / Folder / Archive) ────────────────────
let activeUploadCount = 0;
let uploadMode = "files"; // "files" | "folder" | "archive"
let uploadFiles = [];     // used for "files" and "folder" modes
let archiveFile = null;   // used for "archive" mode
let archiveAnalysis = null; // result of the last /archive/analyze call
let archiveAnalyzeToken = 0; // guards against stale responses if the file changes mid-request
const ARCHIVE_EXTS = ["zip", "7z", "rar"];

function updateUploadIndicator() {
  const indicator = document.getElementById("upload-indicator");
  if (activeUploadCount > 0) {
    indicator.classList.add("active");
  } else {
    indicator.classList.remove("active");
  }
}

// Recursively walks a dropped FileSystemEntry (file or directory), returning
// a flat array of File objects with webkitRelativePath populated so folder
// structure survives drag-and-drop the same way a webkitdirectory <input> does.
function readEntry(entry, basePath = "") {
  return new Promise((resolve) => {
    if (entry.isFile) {
      entry.file((file) => {
        const relPath = basePath + file.name;
        try {
          Object.defineProperty(file, "webkitRelativePath", { value: relPath, configurable: true });
        } catch {
          // Some browsers don't allow overriding; fall back to file.name elsewhere.
        }
        resolve([file]);
      }, () => resolve([]));
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      let collected = [];
      const readBatch = () => {
        reader.readEntries(async (entries) => {
          if (!entries.length) {
            const nested = await Promise.all(collected.map((e) => readEntry(e, basePath + entry.name + "/")));
            resolve(nested.flat());
          } else {
            collected = collected.concat(entries);
            readBatch(); // readEntries must be called repeatedly until it returns empty
          }
        }, () => resolve([]));
      };
      readBatch();
    } else {
      resolve([]);
    }
  });
}

// Reads a DataTransferItemList from a drop event, expanding any directories.
async function expandDataTransferItems(items) {
  const entries = Array.from(items)
    .map((item) => (item.webkitGetAsEntry ? item.webkitGetAsEntry() : null))
    .filter(Boolean);
  if (!entries.length) return { files: [], hadDirectory: false };
  const hadDirectory = entries.some((e) => e.isDirectory);
  const results = await Promise.all(entries.map((e) => readEntry(e)));
  return { files: results.flat(), hadDirectory };
}

function setUploadMode(mode) {
  uploadMode = mode;
  closeUploadMenu();
  const hint = document.getElementById("upload-hint");
  const hints = {
    files: "Any supported file type, a whole folder, or a ZIP / 7z / RAR archive",
    folder: "Folder selected — all files inside will be ingested recursively",
    archive: "Archive selected — it will be extracted and ingested",
  };
  if (hint) hint.textContent = hints[mode] || hints.files;
}

function openUploadMenu() {
  document.getElementById("upload-menu-wrap").classList.add("open");
  document.getElementById("upload-menu-trigger").setAttribute("aria-expanded", "true");
}
function closeUploadMenu() {
  document.getElementById("upload-menu-wrap").classList.remove("open");
  document.getElementById("upload-menu-trigger").setAttribute("aria-expanded", "false");
}

function clearUploadSelection() {
  uploadFiles = [];
  archiveFile = null;
  archiveAnalysis = null;
  renderUploadUI();
}

function renderUploadUI() {
  const preview = document.getElementById("archive-preview");
  if (uploadMode === "archive") {
    renderFileList([]); // hide the flat/tree list
    if (archiveFile) renderArchiveChip(); else if (preview) preview.style.display = "none";
  } else {
    if (preview) preview.style.display = "none";
    renderFileList(uploadFiles);
  }
}

// Guards against the native file picker being triggered twice in quick
// succession (e.g. a click event that fires more than once, or a stray
// second call while a dialog is already open/closing). Only one `.click()`
// is allowed to go through until the window regains focus (which happens
// right as the OS picker closes, cancelled or not) or a short timeout passes.
let filePickerBusy = false;
function openPicker(inputEl) {
  if (filePickerBusy) return;
  filePickerBusy = true;
  inputEl.click();
  const release = () => {
    filePickerBusy = false;
    window.removeEventListener("focus", release);
  };
  window.addEventListener("focus", release, { once: true });
  setTimeout(release, 1000);
}

function initUploadZone() {
  const drop = document.getElementById("upload-drop");
  const fileInput = document.getElementById("file-input");
  const dirInput = document.getElementById("dir-input");
  const archiveInput = document.getElementById("archive-input");
  const btn = document.getElementById("upload-btn");
  const clear = document.getElementById("upload-clear");
  const menuWrap = document.getElementById("upload-menu-wrap");
  const menuTrigger = document.getElementById("upload-menu-trigger");
  const menu = document.getElementById("upload-menu");

  menuTrigger.addEventListener("click", (e) => {
    e.stopPropagation();
    const isOpen = menuWrap.classList.contains("open");
    if (isOpen) closeUploadMenu(); else openUploadMenu();
  });

  menu.querySelectorAll(".upload-menu-item").forEach((item) => {
    item.addEventListener("click", (e) => {
      e.stopPropagation();
      const kind = item.dataset.kind;
      setUploadMode(kind);
      if (kind === "files") openPicker(fileInput);
      else if (kind === "folder") openPicker(dirInput);
      else if (kind === "archive") openPicker(archiveInput);
    });
  });

  document.addEventListener("click", (e) => {
    if (!menuWrap.contains(e.target)) closeUploadMenu();
  });

  drop.addEventListener("click", (e) => {
    if (
      e.target.closest(".file-chip-remove") ||
      e.target.closest(".upload-menu-wrap") ||
      e.target === fileInput ||
      e.target === dirInput ||
      e.target === archiveInput
    ) return;
    setUploadMode("files");
    openPicker(fileInput);
  });

  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("dragover"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("dragover"));
  drop.addEventListener("drop", async (e) => {
    e.preventDefault();
    drop.classList.remove("dragover");

    if (e.dataTransfer.items && e.dataTransfer.items.length) {
      const { files, hadDirectory } = await expandDataTransferItems(e.dataTransfer.items);
      if (!files.length) return;
      if (!hadDirectory && files.length === 1 && ARCHIVE_EXTS.includes(getExt(files[0].name))) {
        setUploadMode("archive");
        setArchive(files[0]);
      } else {
        setUploadMode(hadDirectory ? "folder" : "files");
        addUploadFiles(files);
      }
    } else if (e.dataTransfer.files.length) {
      const dropped = Array.from(e.dataTransfer.files);
      if (dropped.length === 1 && ARCHIVE_EXTS.includes(getExt(dropped[0].name))) {
        setUploadMode("archive");
        setArchive(dropped[0]);
      } else {
        setUploadMode("files");
        addUploadFiles(dropped);
      }
    }
  });

  fileInput.addEventListener("change", () => { addUploadFiles(fileInput.files); fileInput.value = ""; });
  dirInput.addEventListener("change", () => { addUploadFiles(dirInput.files); dirInput.value = ""; });
  archiveInput.addEventListener("change", () => { if (archiveInput.files.length) setArchive(archiveInput.files[0]); archiveInput.value = ""; });

  clear.addEventListener("click", clearUploadSelection);
  btn.addEventListener("click", handleUpload);
}

function addUploadFiles(fileList) {
  for (const f of fileList) {
    const path = f.webkitRelativePath || f.name;
    if (!uploadFiles.some((u) => (u.webkitRelativePath || u.name) === path && u.size === f.size)) {
      uploadFiles.push(f);
    }
  }
  renderUploadUI();
}

function renderFileList(files) {
  const list = document.getElementById("upload-file-list");
  const btn = document.getElementById("upload-btn");
  const clear = document.getElementById("upload-clear");

  list.innerHTML = "";
  list.className = files.length ? "drop-zone-files has-files" : "drop-zone-files";

  if (files.length === 0) {
    if (uploadMode !== "archive") btn.disabled = true;
    clear.style.display = (uploadMode === "archive" && archiveFile) ? "" : "none";
    return;
  }

  const hasFolderPaths = files.some((f) => f.webkitRelativePath && f.webkitRelativePath.includes("/"));
  if (hasFolderPaths) {
    renderFolderTree(list, files);
  } else {
    renderFlatFileList(list, files);
  }

  btn.disabled = false;
  clear.style.display = "";
}

function renderFlatFileList(list, files) {
  const totalSize = files.reduce((sum, f) => sum + f.size, 0);
  const summary = document.createElement("div");
  summary.className = "file-chip";
  summary.innerHTML = `<span class="file-chip-name"><strong>${files.length} file(s) selected</strong></span><span class="file-chip-meta">${formatBytes(totalSize)} total</span>`;
  list.appendChild(summary);

  files.forEach((f, i) => {
    const ext = getExt(f.name);
    const chip = document.createElement("div");
    chip.className = "file-chip";
    chip.innerHTML = `
      <span class="file-chip-icon">${getFileIcon(f.name)}</span>
      <div class="file-chip-info">
        <span class="file-chip-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>
        <span class="file-chip-meta">
          <span class="file-chip-ext ${getExtBadgeClass(ext)}">${ext || "file"}</span>
          <span>${formatBytes(f.size)}</span>
        </span>
      </div>
      <button class="file-chip-remove" data-idx="${i}" title="Remove">&times;</button>
    `;
    list.appendChild(chip);
  });

  list.querySelectorAll(".file-chip-remove").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      uploadFiles.splice(parseInt(btn.dataset.idx), 1);
      renderUploadUI();
    });
  });
}

function renderFolderTree(list, files) {
  // Build a tree keyed by path segments, tracking the originating index in
  // `files` on every leaf so it can be removed individually.
  const tree = {};
  files.forEach((f, idx) => {
    const path = f.webkitRelativePath || f.name;
    const parts = path.split("/");
    let node = tree;
    parts.forEach((part, i) => {
      if (!node.children) node.children = {};
      if (!node.children[part]) node.children[part] = {};
      node = node.children[part];
      if (i === parts.length - 1) {
        node.file = f;
        node.fileIndex = idx;
      }
    });
  });

  const totalSize = files.reduce((sum, f) => sum + f.size, 0);
  const summary = document.createElement("div");
  summary.className = "file-chip";
  summary.innerHTML = `<span class="file-chip-name"><strong>${files.length} file(s) selected</strong></span><span class="file-chip-meta">${formatBytes(totalSize)} total</span>`;
  list.appendChild(summary);

  let rendered = 0;
  const MAX_VISIBLE = 40;

  function renderNode(node, depth) {
    if (!node.children || depth > 3) return;
    Object.entries(node.children).forEach(([name, child]) => {
      if (rendered >= MAX_VISIBLE) return;
      const isDir = !!child.children; // explicit: it's a directory iff it has children in the tree
      const chip = document.createElement("div");
      chip.className = "file-chip";
      chip.style.paddingLeft = `${depth * 16 + 8}px`;

      if (isDir) {
        const itemCount = Object.keys(child.children).length;
        chip.innerHTML = `
          <span class="file-chip-icon" style="color:#f59e0b">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
          </span>
          <span class="file-chip-name">${escapeHtml(name)}</span>
          <span class="file-chip-meta">${itemCount} item${itemCount !== 1 ? "s" : ""}</span>
        `;
        list.appendChild(chip);
        rendered++;
        renderNode(child, depth + 1);
      } else {
        const ext = getExt(name);
        chip.innerHTML = `
          <span class="file-chip-icon">${getFileIcon(name)}</span>
          <div class="file-chip-info">
            <span class="file-chip-name" title="${escapeHtml(name)}">${escapeHtml(name)}</span>
            <span class="file-chip-meta">${formatBytes(child.file?.size || 0)}</span>
          </div>
          <button class="file-chip-remove" data-idx="${child.fileIndex}" title="Remove">&times;</button>
        `;
        list.appendChild(chip);
        rendered++;
      }
    });
  }

  renderNode(tree, 0);

  if (files.length > rendered) {
    const more = document.createElement("div");
    more.className = "file-chip";
    more.style.fontStyle = "italic";
    more.textContent = `...and ${files.length - rendered} more entries not shown`;
    list.appendChild(more);
  }

  list.querySelectorAll(".file-chip-remove").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const idx = parseInt(btn.dataset.idx);
      if (!isNaN(idx)) {
        uploadFiles.splice(idx, 1);
        renderUploadUI();
      }
    });
  });
}

async function handleUpload() {
  if (uploadMode === "archive") return handleArchiveUpload();
  return handleFilesOrFolderUpload();
}

async function handleFilesOrFolderUpload() {
  const statusEl = document.getElementById("upload-status");
  if (!uploadFiles.length) return;

  const isFolder = uploadFiles.some((f) => f.webkitRelativePath && f.webkitRelativePath.includes("/"));
  const fd = new FormData();

  try {
    let res, data;
    if (isFolder) {
      for (const f of uploadFiles) fd.append("files", f, f.webkitRelativePath || f.name);
      fd.append("paths", uploadFiles.map((f) => f.webkitRelativePath || f.name).join("\n"));
      res = await fetch(`${API}/ingest/directory/upload`, { method: "POST", body: fd });
    } else {
      for (const f of uploadFiles) fd.append("files", f);
      res = await fetch(`${API}/ingest/files`, { method: "POST", body: fd });
    }
    data = await res.json();
    if (res.ok && data.job_id) {
      clearUploadSelection();
      startPolling(data.job_id, `${isFolder ? "Ingesting folder" : "Uploading"} — ${data.total_files} file(s)...`);
    } else {
      showMsg(statusEl, data.detail || "Upload failed", false);
      showToast("Upload failed", data.detail || "Unknown error", "error");
    }
  } catch (err) {
    showMsg(statusEl, err.message, false);
    showToast("Upload failed", err.message, "error");
  }
}

initUploadZone();

// ── Ingest: Archive ───────────────────────────────────────────────────────
function renderArchiveChip() {
  const list = document.getElementById("upload-file-list");
  list.innerHTML = "";
  if (!archiveFile) return;
  list.className = "drop-zone-files has-files";
  const ext = getExt(archiveFile.name);
  const chip = document.createElement("div");
  chip.className = "file-chip";
  chip.innerHTML = `
    <span class="file-chip-icon">${getFileIcon(archiveFile.name)}</span>
    <div class="file-chip-info">
      <span class="file-chip-name">${escapeHtml(archiveFile.name)}</span>
      <span class="file-chip-meta">
        <span class="file-chip-ext ${getExtBadgeClass(ext)}">${ext}</span>
        <span>${formatBytes(archiveFile.size)}</span>
      </span>
    </div>
    <button class="file-chip-remove" title="Remove">&times;</button>
  `;
  list.appendChild(chip);
  chip.querySelector(".file-chip-remove").addEventListener("click", (e) => {
    e.stopPropagation();
    archiveFile = null;
    archiveAnalysis = null;
    renderArchive();
  });
}

function setArchive(file) {
  archiveFile = file;
  archiveAnalysis = null;
  renderArchive();
  analyzeArchive(file);
}

async function analyzeArchive(file) {
  const preview = document.getElementById("archive-preview");
  const myToken = ++archiveAnalyzeToken;

  if (preview) {
    preview.innerHTML = `<div class="progress-line current"><div class="spinner spinner-sm"></div> Analyzing archive...</div>`;
    preview.style.display = "block";
  }

  const fd = new FormData();
  fd.append("file", file);

  try {
    const res = await fetch(`${API}/archive/analyze`, { method: "POST", body: fd });
    const data = await res.json();
    if (myToken !== archiveAnalyzeToken) return; // a newer file was selected meanwhile, discard this result

    archiveAnalysis = data;
    renderArchivePreview();
  } catch (err) {
    if (myToken !== archiveAnalyzeToken) return;
    archiveAnalysis = { valid: false, error: err.message, file_count: 0, total_size: 0 };
    renderArchivePreview();
  }
}

function renderArchivePreview() {
  const preview = document.getElementById("archive-preview");
  const btn = document.getElementById("upload-btn");
  if (!preview || !archiveAnalysis) return;
  preview.style.display = "block";

  if (archiveAnalysis.valid) {
    preview.innerHTML = `
      <div class="progress-line ok">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg>
        Valid ${escapeHtml(archiveAnalysis.format)} archive &mdash; ${archiveAnalysis.file_count} file${archiveAnalysis.file_count !== 1 ? "s" : ""}, ${formatBytes(archiveAnalysis.total_size)} uncompressed
      </div>
    `;
    if (btn) btn.disabled = false;
  } else {
    preview.innerHTML = `
      <div class="progress-line err">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
        ${escapeHtml(archiveAnalysis.error || "This archive can't be ingested.")}
      </div>
    `;
    if (btn) btn.disabled = true;
  }
}

function renderArchive() {
  renderArchiveChip();
  const btn = document.getElementById("upload-btn");
  const clear = document.getElementById("upload-clear");
  const preview = document.getElementById("archive-preview");

  if (!archiveFile && preview) preview.style.display = "none";

  // Ingest button stays disabled until analysis confirms the archive is valid
  btn.disabled = !archiveFile || !(archiveAnalysis && archiveAnalysis.valid);
  clear.style.display = archiveFile ? "" : "none";
}

async function handleArchiveUpload() {
  const statusEl = document.getElementById("upload-status");
  if (!archiveFile) return;
  if (!archiveAnalysis || !archiveAnalysis.valid) {
    showToast("Archive not ready", "Waiting for archive analysis to confirm it's valid.", "error");
    return;
  }

  const fd = new FormData();
  fd.append("file", archiveFile);

  try {
    const res = await fetch(`${API}/ingest/archive`, { method: "POST", body: fd });
    const data = await res.json();
    if (res.ok && data.job_id) {
      clearUploadSelection();
      startPolling(data.job_id, `Extracting & ingesting archive...`);
    } else {
      showMsg(statusEl, data.detail || "Upload failed", false);
      showToast("Archive upload failed", data.detail || "Unknown error", "error");
    }
  } catch (err) {
    showMsg(statusEl, err.message, false);
    showToast("Archive upload failed", err.message, "error");
  }
}

// ── Documents ─────────────────────────────────────────────────────────────
let docOffset = 0;
const docLimit = 20;
let selectedDocs = new Set();

function updateDocToolbar() {
  const toolbar = document.getElementById("doc-toolbar");
  const count = document.getElementById("doc-selected-count");
  count.textContent = selectedDocs.size;
  toolbar.classList.toggle("active", selectedDocs.size > 0);
}

async function loadDocuments() {
  const ext = selectedExtFilter;
  const listEl = document.getElementById("doc-list");
  let url = `${API}/documents?offset=${docOffset}&limit=${docLimit}`;
  if (ext) url += `&file_ext=${encodeURIComponent(ext)}`;

  listEl.innerHTML = `
    <div class="skeleton-card skeleton"></div>
    <div class="skeleton-card skeleton"></div>
    <div class="skeleton-card skeleton"></div>
  `;

  try {
    const res = await fetch(url);
    const data = await res.json();

    document.getElementById("doc-page-info").textContent =
      `Showing ${docOffset + 1}-${Math.min(docOffset + docLimit, data.total)} of ${data.total}`;
    document.getElementById("doc-prev").disabled = docOffset === 0;
    document.getElementById("doc-next").disabled = docOffset + docLimit >= data.total;

    listEl.innerHTML = "";
    if (data.documents.length === 0) {
      listEl.innerHTML = `
        <div class="empty-state">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/></svg>
          <h3>No documents indexed</h3>
          <p>Upload files or folders to get started.</p>
        </div>
      `;
      return;
    }

    data.documents.forEach((d) => {
      const ext = getExt(d.filepath);
      const card = document.createElement("div");
      card.className = "doc-card";
      if (selectedDocs.has(d.doc_id)) card.classList.add("selected");

      card.innerHTML = `
        <button class="doc-card-check" data-id="${d.doc_id}" aria-label="Select document">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
        </button>
        <div class="doc-icon">${getFileIcon(d.filepath)}</div>
        <div class="doc-info">
          <div class="doc-filepath" title="${escapeHtml(d.filepath)}">${escapeHtml(d.filepath)}</div>
          <div class="doc-meta">
            <span class="badge ${getExtBadgeClass(ext)}">${ext || "file"}</span>
            <span>${formatBytes(d.file_size)}</span>
            <span>${d.filename}</span>
          </div>
        </div>
        <div class="doc-actions">
          <button class="btn btn-danger btn-sm doc-delete" data-id="${d.doc_id}" title="Delete">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
          </button>
        </div>
      `;
      listEl.appendChild(card);
    });

    // Selection handlers
    listEl.querySelectorAll(".doc-card-check").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const id = btn.dataset.id;
        const card = btn.closest(".doc-card");
        if (selectedDocs.has(id)) {
          selectedDocs.delete(id);
          card.classList.remove("selected");
        } else {
          selectedDocs.add(id);
          card.classList.add("selected");
        }
        updateDocToolbar();
      });
    });

    // Click on card toggles selection
    listEl.querySelectorAll(".doc-card").forEach((card) => {
      card.addEventListener("click", (e) => {
        if (e.target.closest(".doc-delete") || e.target.closest(".doc-card-check")) return;
        const btn = card.querySelector(".doc-card-check");
        btn.click();
      });
    });

    listEl.querySelectorAll(".doc-delete").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm("Delete this document?")) return;
        try {
          await fetch(`${API}/documents/${btn.dataset.id}`, { method: "DELETE" });
          showToast("Deleted", "Document removed from index", "success");
          if (selectedDocs.has(btn.dataset.id)) {
            selectedDocs.delete(btn.dataset.id);
            updateDocToolbar();
          }
          loadDocuments();
        } catch (err) {
          showToast("Delete failed", err.message, "error");
        }
      });
    });
  } catch (err) {
    listEl.innerHTML = `
      <div class="empty-state">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
        <h3>Error loading documents</h3>
        <p>${escapeHtml(err.message)}</p>
      </div>
    `;
  }
}

// Bulk delete
document.getElementById("doc-bulk-delete").addEventListener("click", async () => {
  if (selectedDocs.size === 0) return;
  if (!confirm(`Delete ${selectedDocs.size} selected document(s)? This cannot be undone.`)) return;

  const statusEl = document.getElementById("clear-status");
  showLoading(statusEl, `Deleting ${selectedDocs.size} document(s)...`);

  try {
    const promises = Array.from(selectedDocs).map(id =>
      fetch(`${API}/documents/${id}`, { method: "DELETE" })
    );
    await Promise.all(promises);
    const count = selectedDocs.size;
    selectedDocs.clear();
    updateDocToolbar();
    showMsg(statusEl, `${count} document(s) deleted`, true);
    showToast("Bulk delete complete", `${count} documents removed`, "success");
    loadDocuments();
  } catch (err) {
    showMsg(statusEl, err.message, false);
    showToast("Bulk delete failed", err.message, "error");
  }
});

document.getElementById("doc-prev").addEventListener("click", () => {
  docOffset = Math.max(0, docOffset - docLimit);
  selectedDocs.clear();
  updateDocToolbar();
  loadDocuments();
});

document.getElementById("doc-next").addEventListener("click", () => {
  docOffset += docLimit;
  selectedDocs.clear();
  updateDocToolbar();
  loadDocuments();
});

document.getElementById("doc-refresh").addEventListener("click", () => {
  selectedDocs.clear();
  updateDocToolbar();
  loadDocuments();
});

// ── Extension Filter Dropdown ────────────────────────────────────────────
let selectedExtFilter = "";
let allExtensions = [];
let extensionsLoaded = false;

function applyExtFilter(ext, label) {
  selectedExtFilter = ext;
  document.getElementById("doc-ext-trigger-label").textContent = label;
  closeExtPanel();
  docOffset = 0;
  selectedDocs.clear();
  updateDocToolbar();
  loadDocuments();
}

function renderExtList(filterText = "") {
  const listEl = document.getElementById("doc-ext-list");
  const q = filterText.trim().toLowerCase();
  const items = [{ ext: "", label: "All types" }, ...allExtensions.map((e) => ({ ext: e, label: e }))];
  const filtered = q ? items.filter((i) => i.label.toLowerCase().includes(q)) : items;

  listEl.innerHTML = "";
  if (filtered.length === 0) {
    listEl.innerHTML = `<li class="ext-dropdown-empty">No matching extensions</li>`;
    return;
  }

  filtered.forEach((item) => {
    const li = document.createElement("li");
    li.className = "ext-dropdown-item";
    li.setAttribute("role", "option");
    li.dataset.ext = item.ext;
    if (item.ext === selectedExtFilter) li.classList.add("active");
    li.innerHTML = item.ext
      ? `${getFileIcon("file" + item.ext)}<span>${escapeHtml(item.label)}</span>`
      : `<span class="ext-dropdown-all">${escapeHtml(item.label)}</span>`;
    li.addEventListener("click", () => applyExtFilter(item.ext, item.label || "All types"));
    listEl.appendChild(li);
  });
}

async function loadExtensions() {
  if (extensionsLoaded) return;
  try {
    const res = await fetch(`${API}/documents/extensions`);
    if (res.ok) {
      allExtensions = await res.json();
      extensionsLoaded = true;
    }
  } catch (err) {
    // Non-fatal — dropdown just falls back to "All types" only
  }
  renderExtList(document.getElementById("doc-ext-search").value);
}

function openExtPanel() {
  document.getElementById("doc-ext-dropdown").classList.add("open");
  document.getElementById("doc-ext-trigger").setAttribute("aria-expanded", "true");
  loadExtensions();
  const search = document.getElementById("doc-ext-search");
  search.value = "";
  renderExtList("");
  setTimeout(() => search.focus(), 0);
}

function closeExtPanel() {
  document.getElementById("doc-ext-dropdown").classList.remove("open");
  document.getElementById("doc-ext-trigger").setAttribute("aria-expanded", "false");
}

document.getElementById("doc-ext-trigger").addEventListener("click", (e) => {
  e.stopPropagation();
  const isOpen = document.getElementById("doc-ext-dropdown").classList.contains("open");
  if (isOpen) closeExtPanel();
  else openExtPanel();
});

document.getElementById("doc-ext-search").addEventListener("input", (e) => {
  renderExtList(e.target.value);
});

document.getElementById("doc-ext-search").addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeExtPanel(); }
  e.stopPropagation();
});

document.addEventListener("click", (e) => {
  const dropdown = document.getElementById("doc-ext-dropdown");
  if (dropdown.classList.contains("open") && !dropdown.contains(e.target)) closeExtPanel();
});

// ── Status Dashboard ──────────────────────────────────────────────────────
async function loadStatus() {
  const el = document.getElementById("status-cards");
  el.innerHTML = Array(6).fill(
    `<div class="stat-card"><div class="skeleton skeleton-text short"></div><div class="skeleton skeleton-text medium"></div></div>`
  ).join("");

  try {
    const res = await fetch(`${API}/status?t=${Date.now()}`);
    const data = await res.json();

    const stats = [
      { label: "Indexed Chunks", value: data.points_count?.toLocaleString() || "0", icon: "📄", color: "var(--accent)" },
      { label: "Tracked Files", value: data.tracker_files?.toLocaleString() || "0", icon: "📁", color: "var(--success)" },
      { label: "Unique Files", value: data.unique_files?.toLocaleString() || "0", icon: "🗂️", color: "var(--info)" },
      { label: "File Types", value: data.unique_extensions?.toLocaleString() || "0", icon: "🏷️", color: "var(--warning)" },
      { label: "Content Size", value: formatBytes(data.total_content_size_bytes || 0), icon: "📦", color: "var(--accent)" },
      { label: "DB Size", value: formatBytes(data.db_size_bytes || 0), icon: "💾", color: "var(--warning)" },
      { label: "Last Ingested", value: formatRelativeTime(data.last_ingested_at), icon: "🕒", color: "var(--success)" },
    ];

    el.innerHTML = stats.map(s => `
      <div class="stat-card">
        <div class="stat-label">
          <span style="color:${s.color}">${s.icon}</span>
          ${s.label}
        </div>
        <div class="stat-value">${s.value}</div>
      </div>
    `).join("");
  } catch (err) {
    el.innerHTML = `
      <div class="empty-state">
        <h3>Error loading status</h3>
        <p>${escapeHtml(err.message)}</p>
      </div>
    `;
  }
}

document.getElementById("clear-all-btn").addEventListener("click", async () => {
  const statusEl = document.getElementById("clear-status");
  if (!confirm("Delete ALL indexed documents? This cannot be undone.")) return;
  showLoading(statusEl, "Clearing database...");
  try {
    const res = await fetch(`${API}/documents/all`, { method: "DELETE" });
    const data = await res.json();
    if (res.ok) {
      showMsg(statusEl, "All documents cleared", true);
      showToast("Database cleared", "All documents have been removed", "success");
      await loadStatus();
    } else {
      showMsg(statusEl, data.detail || "Error", false);
      showToast("Clear failed", data.detail || "Unknown error", "error");
    }
  } catch (err) {
    showMsg(statusEl, err.message, false);
    showToast("Clear failed", err.message, "error");
  }
});

// ── Keyboard Shortcuts ────────────────────────────────────────────────────
document.addEventListener("keydown", (e) => {
  // Cmd/Ctrl + K: focus search
  if ((e.metaKey || e.ctrlKey) && e.key === "k") {
    e.preventDefault();
    switchTab("search");
    searchInput.focus();
  }
  // ?: show help toast
  if (e.key === "?" && !e.target.closest("input, textarea, select")) {
    e.preventDefault();
    showToast("Keyboard shortcuts", "⌘K: Focus search • Esc: Close modal", "info", 6000);
  }
});

// ── Init ──────────────────────────────────────────────────────────────────
let _initialized = false;
function init() {
  if (_initialized) return;
  _initialized = true;
  switchTab(getTab());
}
init();