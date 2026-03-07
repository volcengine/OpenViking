const API_BASE = "/console/api/v1";
const SESSION_KEY = "ov_console_api_key";
const THEME_MODE_KEY = "ov_console_theme_mode";
const NAV_COLLAPSED_KEY = "ov_console_nav_collapsed";
const RESULT_COLLAPSED_KEY = "ov_console_result_collapsed";

const PANEL_META = {
  filesystem: {
    title: "FileSystem",
    subtitle: "Browse viking:// paths.",
  },
  find: {
    title: "Find",
    subtitle: "Run semantic retrieval and inspect matched records.",
  },
  "add-resource": {
    title: "Add Resource",
    subtitle: "Submit path or upload data into viking:// resources.",
  },
  "add-memory": {
    title: "Add Memory",
    subtitle: "Store text or conversations as persistent memories.",
  },
  tenants: {
    title: "Tenants",
    subtitle: "Manage accounts and users with explicit write confirmation.",
  },
  monitor: {
    title: "Monitor",
    subtitle: "Inspect runtime and observer status snapshots.",
  },
  settings: {
    title: "Settings",
    subtitle: "Configure session credentials and connection behavior.",
  },
};

const state = {
  activePanel: "filesystem",
  writeEnabled: false,
  fsCurrentUri: "viking://",
  fsHistory: [],
  fsSortField: "uri",
  fsSortDirection: "asc",
  fsViewMode: "list",
  fsTreeData: {},
  fsTreeExpanded: new Set(),
  findRows: [],
  findSortField: "",
  findSortDirection: "asc",
  tenantAccounts: [],
  tenantFilteredAccounts: [],
  tenantUsers: [],
  tenantSelectedAccountId: "",
  tenantAccountsLoaded: false,
  tenantAccountSortField: "account_id",
  tenantAccountSortDirection: "asc",
  tenantUserSortField: "user_id",
  tenantUserSortDirection: "asc",
  tenantConfirmRequest: null,
  themeMode: "dark",
  navCollapsed: false,
  resultCollapsed: false,
};

const elements = {
  workspace: document.querySelector(".workspace"),
  shell: document.querySelector(".shell"),
  content: document.querySelector(".content"),
  tabsTop: document.querySelector(".tabs-top"),
  panelStack: document.querySelector(".panel-stack"),
  sidebar: document.querySelector(".sidebar"),
  resultCard: document.querySelector(".result-card"),
  sidebarResizer: document.getElementById("sidebarResizer"),
  outputResizer: document.getElementById("outputResizer"),
  apiKeyInput: document.getElementById("apiKeyInput"),
  saveKeyBtn: document.getElementById("saveKeyBtn"),
  clearKeyBtn: document.getElementById("clearKeyBtn"),
  connectionHint: document.getElementById("connectionHint"),
  writeBadge: document.getElementById("writeBadge"),
  output: document.getElementById("output"),
  tabs: document.querySelectorAll(".tab"),
  panels: document.querySelectorAll(".panel"),
  fsBackBtn: document.getElementById("fsBackBtn"),
  fsUpBtn: document.getElementById("fsUpBtn"),
  fsRefreshBtn: document.getElementById("fsRefreshBtn"),
  fsModeListBtn: document.getElementById("fsModeListBtn"),
  fsModeTreeBtn: document.getElementById("fsModeTreeBtn"),
  fsGoBtn: document.getElementById("fsGoBtn"),
  fsCurrentUri: document.getElementById("fsCurrentUri"),
  fsEntries: document.getElementById("fsEntries"),
  fsSortHeaders: document.querySelectorAll(".fs-sort-btn"),
  fsTable: document.querySelector(".fs-table"),
  fsTableWrap: document.querySelector(".fs-table-wrap"),
  fsTree: document.getElementById("fsTree"),
  findQuery: document.getElementById("findQuery"),
  findTarget: document.getElementById("findTarget"),
  findBtn: document.getElementById("findBtn"),
  findResultsHead: document.getElementById("findResultsHead"),
  findResultsBody: document.getElementById("findResultsBody"),
  addResourcePath: document.getElementById("addResourcePath"),
  addResourceFile: document.getElementById("addResourceFile"),
  addResourceTarget: document.getElementById("addResourceTarget"),
  addResourceWait: document.getElementById("addResourceWait"),
  addResourceStrict: document.getElementById("addResourceStrict"),
  addResourceUploadMedia: document.getElementById("addResourceUploadMedia"),
  addResourceTimeout: document.getElementById("addResourceTimeout"),
  addResourceIgnoreDirs: document.getElementById("addResourceIgnoreDirs"),
  addResourceInclude: document.getElementById("addResourceInclude"),
  addResourceExclude: document.getElementById("addResourceExclude"),
  addResourceReason: document.getElementById("addResourceReason"),
  addResourceInstruction: document.getElementById("addResourceInstruction"),
  addResourceBtn: document.getElementById("addResourceBtn"),
  addResourceUploadBtn: document.getElementById("addResourceUploadBtn"),
  addMemoryInput: document.getElementById("addMemoryInput"),
  addMemoryBtn: document.getElementById("addMemoryBtn"),
  tenantAccountSearch: document.getElementById("tenantAccountSearch"),
  tenantRefreshAccountsBtn: document.getElementById("tenantRefreshAccountsBtn"),
  tenantCreateAccountBtn: document.getElementById("tenantCreateAccountBtn"),
  tenantCreateAccountId: document.getElementById("tenantCreateAccountId"),
  tenantCreateAdminUserId: document.getElementById("tenantCreateAdminUserId"),
  tenantAccountsBody: document.getElementById("tenantAccountsBody"),
  tenantCurrentAccount: document.getElementById("tenantCurrentAccount"),
  tenantAddUserBtn: document.getElementById("tenantAddUserBtn"),
  tenantAddUserId: document.getElementById("tenantAddUserId"),
  tenantAddUserRole: document.getElementById("tenantAddUserRole"),
  tenantUsersBody: document.getElementById("tenantUsersBody"),
  tenantAccountSortBtns: document.querySelectorAll("[data-tenant-account-sort]"),
  tenantUserSortBtns: document.querySelectorAll("[data-tenant-user-sort]"),
  tenantConfirmModal: document.getElementById("tenantConfirmModal"),
  tenantConfirmTitle: document.getElementById("tenantConfirmTitle"),
  tenantConfirmMessage: document.getElementById("tenantConfirmMessage"),
  tenantConfirmLabel: document.getElementById("tenantConfirmLabel"),
  tenantConfirmInput: document.getElementById("tenantConfirmInput"),
  tenantConfirmError: document.getElementById("tenantConfirmError"),
  tenantConfirmActionBtn: document.getElementById("tenantConfirmActionBtn"),
  tenantConfirmCancelBtn: document.getElementById("tenantConfirmCancelBtn"),
  systemBtn: document.getElementById("systemBtn"),
  observerBtn: document.getElementById("observerBtn"),
  monitorResults: document.getElementById("monitorResults"),
  navToggleBtn: document.getElementById("navToggleBtn"),
  resultToggleBtn: document.getElementById("resultToggleBtn"),
  clearOutputBtn: document.getElementById("clearOutputBtn"),
  themeButtons: document.querySelectorAll("[data-theme-mode]"),
  contentTitle: document.querySelector(".title-block h1"),
  contentSubtitle: document.querySelector(".title-block .subtitle"),
};

const layoutLimits = {
  minSidebar: 200,
  maxSidebar: 560,
  minPanel: 0,
  minResult: 48,
};

function readLocalStorage(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (_error) {
    return null;
  }
}

function writeLocalStorage(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (_error) {
    // Ignore storage failures in private mode or restricted browsers.
  }
}

function prefersDarkTheme() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function resolveThemeMode(mode) {
  if (mode === "light") {
    return "light";
  }
  if (mode === "system") {
    return prefersDarkTheme() ? "dark" : "light";
  }
  return "dark";
}

function updateThemeButtons() {
  for (const button of elements.themeButtons) {
    const selected = button.dataset.themeMode === state.themeMode;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", selected ? "true" : "false");
  }
}

function applyThemeMode(mode, { persist = true } = {}) {
  const normalized = mode === "light" || mode === "system" ? mode : "dark";
  state.themeMode = normalized;
  const resolved = resolveThemeMode(normalized);
  document.documentElement.setAttribute("data-theme", resolved);
  updateThemeButtons();
  if (persist) {
    writeLocalStorage(THEME_MODE_KEY, normalized);
  }
}

function applyShellStateClasses() {
  if (!elements.shell) {
    return;
  }
  elements.shell.classList.toggle("shell--nav-collapsed", state.navCollapsed);
  elements.shell.classList.toggle("shell--result-collapsed", state.resultCollapsed);
}

function setNavCollapsed(collapsed, { persist = true } = {}) {
  state.navCollapsed = Boolean(collapsed);
  applyShellStateClasses();
  if (persist) {
    writeLocalStorage(NAV_COLLAPSED_KEY, state.navCollapsed ? "1" : "0");
  }
}

function setResultCollapsed(collapsed, { persist = true } = {}) {
  state.resultCollapsed = Boolean(collapsed);
  applyShellStateClasses();
  if (elements.resultToggleBtn) {
    elements.resultToggleBtn.textContent = state.resultCollapsed ? "Show Result" : "Hide Result";
  }
  if (persist) {
    writeLocalStorage(RESULT_COLLAPSED_KEY, state.resultCollapsed ? "1" : "0");
  }
}

function setOutput(value) {
  const content = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  elements.output.textContent = content;
}

function setActivePanel(panel) {
  state.activePanel = panel;
  for (const tab of elements.tabs) {
    tab.classList.toggle("active", tab.dataset.panel === panel);
  }
  for (const panelNode of elements.panels) {
    panelNode.classList.toggle("active", panelNode.id === `panel-${panel}`);
  }

  const meta = PANEL_META[panel];
  if (meta && elements.contentTitle && elements.contentSubtitle) {
    elements.contentTitle.textContent = `OpenViking ${meta.title}`;
    elements.contentSubtitle.textContent = meta.subtitle;
  }

  if (window.matchMedia("(max-width: 900px)").matches) {
    setNavCollapsed(true);
  }

  // If a confirmation dialog was left open, never carry it across panel switches.
  if (elements.tenantConfirmModal && !elements.tenantConfirmModal.hidden) {
    closeTenantConfirmModal();
  }

  if (panel === "tenants") {
    ensureTenantsLoaded().catch((error) => {
      setOutput(error.message);
    });
  }
}

function getApiKey() {
  return window.sessionStorage.getItem(SESSION_KEY) || "";
}

function updateConnectionHint() {
  const key = getApiKey();
  elements.connectionHint.textContent = key
    ? `API key loaded in session (${key.length} chars).`
    : "No API key in session.";
}

function truncateText(value, maxLength = 4000) {
  const text = String(value || "");
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength)}\n... (truncated, ${text.length} chars total)`;
}

function isJsonLikeContentType(contentType) {
  const value = (contentType || "").toLowerCase();
  return value.includes("application/json") || value.includes("+json");
}

async function callConsole(path, options = {}) {
  const headers = {
    ...(options.headers || {}),
  };

  if (!(options.body instanceof FormData)) {
    headers["Content-Type"] = headers["Content-Type"] || "application/json";
  }

  const apiKey = getApiKey();
  if (apiKey) {
    headers["X-API-Key"] = apiKey;
  }

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`NETWORK_ERROR: ${message}`);
  }

  const contentType = response.headers.get("content-type") || "";
  const status = response.status;

  let payload = null;
  let rawText = "";

  if (status === 204 || status === 205) {
    payload = { status: "ok", result: null };
  } else if (isJsonLikeContentType(contentType)) {
    const clone = response.clone();
    try {
      payload = await response.json();
    } catch (_error) {
      rawText = await clone.text().catch(() => "");
      payload = response.ok
        ? { status: "ok", result: rawText }
        : {
            status: "error",
            error: {
              code: "BAD_RESPONSE",
              message: "Invalid JSON response from console",
              detail: truncateText(rawText, 2000),
            },
          };
    }
  } else {
    rawText = await response.text().catch(() => "");
    payload = response.ok
      ? { status: "ok", result: rawText }
      : {
          status: "error",
          error: {
            code: "HTTP_ERROR",
            message: rawText ? truncateText(rawText, 2000) : `Request failed with status ${status}`,
          },
        };
  }

  if (!response.ok) {
    const code = payload?.error?.code || "ERROR";
    const message =
      payload?.error?.message || `Request failed with status ${response.status} ${response.statusText}`;
    const missingApiKey =
      code === "UNAUTHENTICATED" && String(message).toLowerCase().includes("missing api key");
    const hint = missingApiKey ? " Please go to Settings and set X-API-Key." : "";
    throw new Error(`${code}: ${message}${hint}`);
  }

  return payload;
}

function normalizeDirUri(uri) {
  const value = (uri || "").trim();
  if (!value) {
    return "viking://";
  }
  if (value === "viking://") {
    return value;
  }
  return value.endsWith("/") ? value : `${value}/`;
}

function parentUri(uri) {
  const normalized = normalizeDirUri(uri);
  if (normalized === "viking://") {
    return normalized;
  }

  const scheme = "viking://";
  if (!normalized.startsWith(scheme)) {
    return scheme;
  }

  const withoutTrailingSlash = normalized.slice(0, -1);
  const body = withoutTrailingSlash.slice(scheme.length);
  if (!body.includes("/")) {
    return scheme;
  }

  const prefix = body.slice(0, body.lastIndexOf("/") + 1);
  return `${scheme}${prefix}`;
}

function joinUri(baseUri, child) {
  const raw = String(child || "").trim();
  if (!raw) {
    return normalizeDirUri(baseUri);
  }
  if (raw.startsWith("viking://")) {
    return raw;
  }

  const normalizedBase = normalizeDirUri(baseUri);
  const cleanedChild = raw.replace(/^\//, "");
  return `${normalizedBase}${cleanedChild}`;
}

function pickFirstNonEmpty(candidates) {
  for (const candidate of candidates) {
    if (candidate !== undefined && candidate !== null && String(candidate).trim() !== "") {
      return candidate;
    }
  }
  return null;
}

function normalizeFsEntries(result, currentUri) {
  const toEntry = (item) => {
    if (typeof item === "string") {
      const rawName = item.trim();
      const isDir = rawName.endsWith("/");
      const resolvedUri = joinUri(currentUri, rawName);
      return {
        uri: isDir ? normalizeDirUri(resolvedUri) : resolvedUri,
        size: null,
        isDir,
        modTime: null,
        abstract: "",
      };
    }

    if (item && typeof item === "object") {
      const baseLabel =
        item.name || item.path || item.relative_path || item.uri || item.id || JSON.stringify(item);
      const isDir =
        Boolean(item.is_dir) ||
        Boolean(item.isDir) ||
        item.type === "dir" ||
        item.type === "directory" ||
        item.kind === "dir" ||
        String(baseLabel).endsWith("/");
      const rawUri = item.uri || item.path || item.relative_path || baseLabel;
      const resolvedUri = joinUri(currentUri, rawUri);
      const size = pickFirstNonEmpty([
        item.size,
        item.size_bytes,
        item.content_length,
        item.contentLength,
        item.bytes,
      ]);
      const modTime = pickFirstNonEmpty([
        item.modTime,
        item.mod_time,
        item.mtime,
        item.modified_at,
        item.modifiedAt,
        item.updated_at,
        item.updatedAt,
        item.last_modified,
        item.lastModified,
        item.timestamp,
        item.time,
      ]);
      const abstract = pickFirstNonEmpty([
        item.abstract,
        item.summary,
        item.description,
        item.desc,
      ]);

      return {
        uri: isDir ? normalizeDirUri(resolvedUri) : resolvedUri,
        size,
        isDir,
        modTime,
        abstract: abstract === null ? "" : String(abstract),
      };
    }

    return {
      uri: joinUri(currentUri, String(item)),
      size: null,
      isDir: false,
      modTime: null,
      abstract: "",
    };
  };

  if (Array.isArray(result)) {
    return result.map(toEntry);
  }

  if (result && typeof result === "object") {
    const candidates = [result.entries, result.items, result.children, result.results];
    for (const candidate of candidates) {
      if (Array.isArray(candidate)) {
        return candidate.map(toEntry);
      }
    }
  }

  if (typeof result === "string") {
    return result
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map(toEntry);
  }

  return [];
}

function normalizeSortString(value) {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value).toLowerCase();
}

function toSortableNumber(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number.parseFloat(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return null;
}

function toSortableTime(value) {
  if (!value) {
    return null;
  }

  const date = new Date(value);
  if (!Number.isNaN(date.getTime())) {
    return date.getTime();
  }
  return toSortableNumber(value);
}

function compareNullable(left, right, compareFn) {
  const leftMissing = left === null || left === undefined || left === "";
  const rightMissing = right === null || right === undefined || right === "";
  if (leftMissing && rightMissing) {
    return 0;
  }
  if (leftMissing) {
    return 1;
  }
  if (rightMissing) {
    return -1;
  }
  return compareFn(left, right);
}

function compareFsEntries(left, right, field) {
  switch (field) {
    case "size":
      return compareNullable(left.size, right.size, (a, b) => {
        const leftNum = toSortableNumber(a);
        const rightNum = toSortableNumber(b);
        if (leftNum !== null && rightNum !== null) {
          return leftNum - rightNum;
        }
        return normalizeSortString(a).localeCompare(normalizeSortString(b));
      });
    case "isDir":
      return Number(left.isDir) - Number(right.isDir);
    case "modTime":
      return compareNullable(left.modTime, right.modTime, (a, b) => {
        const leftTime = toSortableTime(a);
        const rightTime = toSortableTime(b);
        if (leftTime !== null && rightTime !== null) {
          return leftTime - rightTime;
        }
        return normalizeSortString(a).localeCompare(normalizeSortString(b));
      });
    case "abstract":
      return compareNullable(left.abstract, right.abstract, (a, b) =>
        normalizeSortString(a).localeCompare(normalizeSortString(b))
      );
    case "uri":
    default:
      return normalizeSortString(left.uri).localeCompare(normalizeSortString(right.uri));
  }
}

function sortFilesystemEntries(entries) {
  const sorted = [...entries].sort((left, right) =>
    compareFsEntries(left, right, state.fsSortField)
  );
  if (state.fsSortDirection === "desc") {
    sorted.reverse();
  }
  return sorted;
}

function updateFilesystemSortHeaders() {
  for (const button of elements.fsSortHeaders) {
    const field = button.dataset.fsSort || "";
    const isActive = field === state.fsSortField;
    button.classList.toggle("active", isActive);
    button.setAttribute(
      "aria-sort",
      isActive ? (state.fsSortDirection === "asc" ? "ascending" : "descending") : "none"
    );
    const suffix = !isActive ? "" : state.fsSortDirection === "asc" ? " ↑" : " ↓";
    button.textContent = `${field}${suffix}`;
  }
}

function bindFilesystemSort() {
  for (const button of elements.fsSortHeaders) {
    button.addEventListener("click", async () => {
      const field = button.dataset.fsSort;
      if (!field) {
        return;
      }

      if (state.fsSortField === field) {
        state.fsSortDirection = state.fsSortDirection === "asc" ? "desc" : "asc";
      } else {
        state.fsSortField = field;
        state.fsSortDirection = "asc";
      }

      updateFilesystemSortHeaders();

      try {
        await loadFilesystem(state.fsCurrentUri);
      } catch (error) {
        setOutput(error.message);
      }
    });
  }
}

function initFsColumnResize() {
  if (!elements.fsTable) {
    return;
  }

  const headers = elements.fsTable.querySelectorAll("thead th");
  for (const header of headers) {
    if (header.dataset.resizable === "false") {
      continue;
    }
    if (header.querySelector(".fs-col-resizer")) {
      continue;
    }

    const handle = document.createElement("div");
    handle.className = "fs-col-resizer";
    handle.setAttribute("role", "separator");
    handle.setAttribute("aria-orientation", "vertical");
    handle.setAttribute("aria-label", "Resize column");
    header.appendChild(handle);

    handle.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      event.stopPropagation();
      document.body.classList.add("dragging-fs-column");

      const startX = event.clientX;
      const startWidth = header.getBoundingClientRect().width;
      const minWidth = Number.parseFloat(header.dataset.minWidth || "90");

      handle.setPointerCapture(event.pointerId);

      const onMove = (moveEvent) => {
        const nextWidth = clamp(startWidth + (moveEvent.clientX - startX), minWidth, 1200);
        header.style.width = `${nextWidth}px`;
        header.style.minWidth = `${nextWidth}px`;
      };

      const onUp = () => {
        handle.removeEventListener("pointermove", onMove);
        handle.removeEventListener("pointerup", onUp);
        handle.removeEventListener("pointercancel", onUp);
        document.body.classList.remove("dragging-fs-column");
        handle.releasePointerCapture(event.pointerId);
      };

      handle.addEventListener("pointermove", onMove);
      handle.addEventListener("pointerup", onUp);
      handle.addEventListener("pointercancel", onUp);
    });
  }
}

function normalizeReadContent(result) {
  if (typeof result === "string") {
    return result;
  }
  if (Array.isArray(result)) {
    return result.map((item) => String(item)).join("\n");
  }
  if (result && typeof result === "object") {
    const content = pickFirstNonEmpty([
      result.content,
      result.text,
      result.body,
      result.value,
      result.data,
    ]);
    if (content !== null) {
      return typeof content === "string" ? content : JSON.stringify(content, null, 2);
    }
  }
  return JSON.stringify(result, null, 2);
}

async function readFilesystemFile(entry) {
  const uri = String(entry?.uri || "").replace(/\/$/, "");
  if (!uri) {
    throw new Error("Invalid file uri.");
  }

  setOutput(`Reading ${uri} ...`);
  const payload = await callConsole(
    `/ov/content/read?uri=${encodeURIComponent(uri)}&offset=0&limit=-1`,
    { method: "GET" }
  );
  const content = normalizeReadContent(payload.result);
  setOutput(content && content.trim() ? content : "(empty file)");
}

async function statFilesystemResource(entry) {
  let uri = String(entry?.uri || "").trim();
  if (!uri) {
    throw new Error("Invalid resource uri.");
  }
  if (uri !== "viking://") {
    uri = uri.replace(/\/$/, "");
  }

  const payload = await callConsole(`/ov/fs/stat?uri=${encodeURIComponent(uri)}`, { method: "GET" });
  setOutput(payload);
}

function renderFilesystemEntries(target, rows, onOpen, onOpenContent) {
  target.innerHTML = "";

  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 6;
    td.className = "fs-empty";
    td.textContent = "No data";
    tr.appendChild(td);
    target.appendChild(tr);
    return;
  }

  for (const row of rows) {
    const tr = document.createElement("tr");

    const actionCell = document.createElement("td");
    actionCell.className = "fs-col-action";
    const openBtn = document.createElement("button");
    openBtn.type = "button";
    openBtn.className = "fs-open-btn";
    openBtn.title = "Show stat info";
    openBtn.setAttribute("aria-label", `Show stat info for ${row.uri}`);
    openBtn.textContent = "ⓘ";
    openBtn.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      try {
        await onOpenContent(row);
      } catch (error) {
        setOutput(error.message);
      }
    });
    actionCell.appendChild(openBtn);
    tr.appendChild(actionCell);

    const uriCell = document.createElement("td");
    uriCell.className = "fs-col-uri";
    const uriBtn = document.createElement("button");
    uriBtn.type = "button";
    uriBtn.className = "fs-uri-btn";
    uriBtn.textContent = row.uri || "-";
    uriBtn.addEventListener("click", () => onOpen(row));
    uriCell.appendChild(uriBtn);
    tr.appendChild(uriCell);

    const sizeCell = document.createElement("td");
    sizeCell.className = "fs-col-size";
    sizeCell.textContent = row.size === null || row.size === undefined || row.size === "" ? "-" : String(row.size);
    tr.appendChild(sizeCell);

    const dirCell = document.createElement("td");
    dirCell.className = "fs-col-dir";
    dirCell.textContent = row.isDir ? "true" : "false";
    tr.appendChild(dirCell);

    const modTimeCell = document.createElement("td");
    modTimeCell.className = "fs-col-mod-time";
    modTimeCell.textContent =
      row.modTime === null || row.modTime === undefined || row.modTime === ""
        ? "-"
        : String(row.modTime);
    tr.appendChild(modTimeCell);

    const abstractCell = document.createElement("td");
    abstractCell.className = "fs-col-abstract";
    abstractCell.textContent = row.abstract || "-";
    tr.appendChild(abstractCell);

    target.appendChild(tr);
  }
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function extractDeepestObjectArray(value) {
  const best = { depth: -1, rows: null };

  const visit = (current, depth) => {
    if (Array.isArray(current)) {
      if (current.length > 0 && current.every((item) => isRecord(item))) {
        if (depth > best.depth) {
          best.depth = depth;
          best.rows = current;
        }
      }

      for (const item of current) {
        visit(item, depth + 1);
      }
      return;
    }

    if (!isRecord(current)) {
      return;
    }

    for (const nested of Object.values(current)) {
      visit(nested, depth + 1);
    }
  };

  visit(value, 0);
  return best.rows;
}

function normalizeFindRows(result) {
  if (Array.isArray(result)) {
    return result.map((item) => (isRecord(item) ? item : { value: item }));
  }

  if (isRecord(result)) {
    const typedBucketKeys = ["memories", "resources", "skills"];
    const hasTypedBuckets = typedBucketKeys.some((key) => Array.isArray(result[key]));
    if (hasTypedBuckets) {
      const typedRows = [];
      for (const key of typedBucketKeys) {
        const rows = Array.isArray(result[key]) ? result[key] : [];
        for (const row of rows) {
          const normalized = isRecord(row) ? row : { value: row };
          typedRows.push({
            ...normalized,
            context_type:
              normalized.context_type || (key === "memories" ? "memory" : key.slice(0, -1)),
          });
        }
      }
      return typedRows;
    }

    const topLevelArrays = [
      result.results,
      result.items,
      result.matches,
      result.hits,
      result.rows,
      result.entries,
      result.data,
    ];
    for (const rows of topLevelArrays) {
      if (Array.isArray(rows)) {
        return rows.map((item) => (isRecord(item) ? item : { value: item }));
      }
    }

    const deepestRows = extractDeepestObjectArray(result);
    if (deepestRows) {
      return deepestRows;
    }

    return [result];
  }

  if (result === null || result === undefined) {
    return [];
  }

  return [{ value: result }];
}

function collectFindColumns(rows) {
  const columns = [];
  const seen = new Set();

  for (const row of rows) {
    if (!isRecord(row)) {
      continue;
    }

    for (const key of Object.keys(row)) {
      if (!seen.has(key)) {
        seen.add(key);
        columns.push(key);
      }
    }
  }

  return columns;
}

function formatFindCellValue(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function toFindComparable(value) {
  if (value === null || value === undefined || value === "") {
    return { missing: true, type: "missing", value: "" };
  }

  if (typeof value === "number" && Number.isFinite(value)) {
    return { missing: false, type: "number", value };
  }

  if (typeof value === "boolean") {
    return { missing: false, type: "number", value: Number(value) };
  }

  if (typeof value === "string") {
    const trimmed = value.trim();
    const asNumber = Number.parseFloat(trimmed);
    if (trimmed !== "" && Number.isFinite(asNumber)) {
      return { missing: false, type: "number", value: asNumber };
    }

    const asDate = new Date(trimmed);
    if (!Number.isNaN(asDate.getTime())) {
      return { missing: false, type: "date", value: asDate.getTime() };
    }

    return { missing: false, type: "string", value: trimmed.toLowerCase() };
  }

  return { missing: false, type: "string", value: JSON.stringify(value).toLowerCase() };
}

function compareFindValues(left, right) {
  const leftValue = toFindComparable(left);
  const rightValue = toFindComparable(right);

  if (leftValue.missing && rightValue.missing) {
    return 0;
  }
  if (leftValue.missing) {
    return 1;
  }
  if (rightValue.missing) {
    return -1;
  }

  if (leftValue.type === rightValue.type && (leftValue.type === "number" || leftValue.type === "date")) {
    return leftValue.value - rightValue.value;
  }

  return String(leftValue.value).localeCompare(String(rightValue.value));
}

function sortFindRows(rows, column, direction) {
  const sorted = [...rows].sort((left, right) => {
    const leftCell = isRecord(left) ? left[column] : undefined;
    const rightCell = isRecord(right) ? right[column] : undefined;
    return compareFindValues(leftCell, rightCell);
  });

  if (direction === "desc") {
    sorted.reverse();
  }
  return sorted;
}

function renderFindTable(rows) {
  state.findRows = rows;
  elements.findResultsHead.innerHTML = "";
  elements.findResultsBody.innerHTML = "";

  const columns = collectFindColumns(rows);
  if (!columns.length) {
    columns.push("value");
  }

  if (!state.findSortField || !columns.includes(state.findSortField)) {
    state.findSortField = columns[0];
    state.findSortDirection = "asc";
  }

  const headerRow = document.createElement("tr");
  for (const column of columns) {
    const th = document.createElement("th");
    th.scope = "col";

    const sortBtn = document.createElement("button");
    sortBtn.type = "button";
    sortBtn.className = "find-sort-btn";
    sortBtn.dataset.findSort = column;

    const isActive = state.findSortField === column;
    const sortLabel = isActive ? (state.findSortDirection === "asc" ? " ↑" : " ↓") : "";
    sortBtn.textContent = `${column}${sortLabel}`;
    sortBtn.setAttribute(
      "aria-sort",
      isActive ? (state.findSortDirection === "asc" ? "ascending" : "descending") : "none"
    );

    sortBtn.addEventListener("click", () => {
      if (state.findSortField === column) {
        state.findSortDirection = state.findSortDirection === "asc" ? "desc" : "asc";
      } else {
        state.findSortField = column;
        state.findSortDirection = "asc";
      }
      renderFindTable(state.findRows);
    });

    th.appendChild(sortBtn);
    headerRow.appendChild(th);
  }
  elements.findResultsHead.appendChild(headerRow);

  if (!rows.length) {
    const emptyRow = document.createElement("tr");
    const emptyCell = document.createElement("td");
    emptyCell.colSpan = columns.length;
    emptyCell.className = "find-empty";
    emptyCell.textContent = "No data";
    emptyRow.appendChild(emptyCell);
    elements.findResultsBody.appendChild(emptyRow);
    return;
  }

  const sortedRows = sortFindRows(rows, state.findSortField, state.findSortDirection);
  for (const row of sortedRows) {
    const tr = document.createElement("tr");
    for (const column of columns) {
      const td = document.createElement("td");
      const cellValue = isRecord(row) ? row[column] : undefined;
      td.textContent = formatFindCellValue(cellValue);
      tr.appendChild(td);
    }
    elements.findResultsBody.appendChild(tr);
  }
}

function renderList(target, rows, onClick) {
  target.innerHTML = "";
  if (!rows.length) {
    const empty = document.createElement("li");
    empty.innerHTML = '<div class="row-item">No data</div>';
    target.appendChild(empty);
    return;
  }

  for (const row of rows) {
    const li = document.createElement("li");
    if (onClick) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = row.label;
      button.addEventListener("click", () => onClick(row));
      li.appendChild(button);
    } else {
      const div = document.createElement("div");
      div.className = "row-item";
      div.textContent = row.label;
      li.appendChild(div);
    }
    target.appendChild(li);
  }
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function syncWriteControls() {
  const writeButtons = document.querySelectorAll("[data-tenant-write]");
  for (const button of writeButtons) {
    button.disabled = !state.writeEnabled;
  }
}

function initResizablePanes() {
  const rootStyle = document.documentElement.style;

  if (elements.sidebarResizer && elements.sidebar) {
    elements.sidebarResizer.addEventListener("pointerdown", (event) => {
      if (window.matchMedia("(max-width: 900px)").matches) {
        return;
      }
      event.preventDefault();
      document.body.classList.add("dragging-sidebar");
      elements.sidebarResizer.setPointerCapture(event.pointerId);
      const startX = event.clientX;
      const startWidth = elements.sidebar.getBoundingClientRect().width;

      const onMove = (moveEvent) => {
        const nextWidth = clamp(
          startWidth + (moveEvent.clientX - startX),
          layoutLimits.minSidebar,
          layoutLimits.maxSidebar
        );
        rootStyle.setProperty("--sidebar-width", `${nextWidth}px`);
      };

      const onUp = () => {
        elements.sidebarResizer.removeEventListener("pointermove", onMove);
        elements.sidebarResizer.removeEventListener("pointerup", onUp);
        elements.sidebarResizer.removeEventListener("pointercancel", onUp);
        document.body.classList.remove("dragging-sidebar");
        elements.sidebarResizer.releasePointerCapture(event.pointerId);
      };

      elements.sidebarResizer.addEventListener("pointermove", onMove);
      elements.sidebarResizer.addEventListener("pointerup", onUp);
      elements.sidebarResizer.addEventListener("pointercancel", onUp);
    });
  }

  if (elements.outputResizer && elements.resultCard) {
    elements.outputResizer.addEventListener("pointerdown", (event) => {
      if (window.matchMedia("(max-width: 900px)").matches) {
        return;
      }
      event.preventDefault();
      document.body.classList.add("dragging-output");
      elements.outputResizer.setPointerCapture(event.pointerId);
      const startY = event.clientY;
      const startHeight =
        elements.panelStack?.getBoundingClientRect().height || layoutLimits.minPanel;

      const onMove = (moveEvent) => {
        const contentHeight = elements.content?.getBoundingClientRect().height || window.innerHeight;
        const tabsHeight = elements.tabsTop?.getBoundingClientRect().height || 0;
        const resizerHeight = elements.outputResizer.getBoundingClientRect().height || 8;
        const rowGap = Number.parseFloat(
          window.getComputedStyle(elements.content || document.body).rowGap || "0"
        );
        const totalGap = Number.isFinite(rowGap) ? rowGap * 3 : 0;
        const maxPanel = Math.max(
          layoutLimits.minPanel,
          contentHeight - tabsHeight - resizerHeight - layoutLimits.minResult - totalGap
        );
        const nextHeight = clamp(
          startHeight + (moveEvent.clientY - startY),
          layoutLimits.minPanel,
          maxPanel
        );
        rootStyle.setProperty("--panel-height", `${nextHeight}px`);
      };

      const onUp = () => {
        elements.outputResizer.removeEventListener("pointermove", onMove);
        elements.outputResizer.removeEventListener("pointerup", onUp);
        elements.outputResizer.removeEventListener("pointercancel", onUp);
        document.body.classList.remove("dragging-output");
        elements.outputResizer.releasePointerCapture(event.pointerId);
      };

      elements.outputResizer.addEventListener("pointermove", onMove);
      elements.outputResizer.addEventListener("pointerup", onUp);
      elements.outputResizer.addEventListener("pointercancel", onUp);
    });
  }
}

function buildFsTreeItem(entry, depth) {
  const uriStr = entry.uri || "";
  const trimmed = uriStr.replace(/\/$/, "");
  const lastSlash = trimmed.lastIndexOf("/");
  const displayName = lastSlash >= 0 ? trimmed.slice(lastSlash + 1) || trimmed : trimmed;

  const item = document.createElement("div");
  item.className = `fs-tree-item${entry.isDir ? " fs-tree-item--dir" : ""}`;
  item.style.paddingLeft = `${10 + depth * 16}px`;

  // ⓘ button — leftmost, matches list view action column
  const infoBtn = document.createElement("button");
  infoBtn.type = "button";
  infoBtn.className = "fs-tree-info-btn";
  infoBtn.textContent = "ⓘ";
  infoBtn.title = "Show stat info";
  infoBtn.setAttribute("aria-label", `Show stat info for ${uriStr}`);
  infoBtn.addEventListener("click", async (event) => {
    event.stopPropagation();
    try {
      await statFilesystemResource(entry);
    } catch (error) {
      setOutput(error.message);
    }
  });
  item.appendChild(infoBtn);

  // collapse/expand arrow (dirs only; files get a fixed-width placeholder)
  const toggle = document.createElement("span");
  toggle.className = "fs-tree-toggle";
  toggle.setAttribute("aria-hidden", "true");
  toggle.textContent = entry.isDir ? (state.fsTreeExpanded.has(entry.uri) ? "▼" : "▶") : "";
  item.appendChild(toggle);

  const name = document.createElement("span");
  name.className = "fs-tree-name";
  name.textContent = displayName;
  name.title = uriStr;
  item.appendChild(name);

  item.addEventListener("click", async () => {
    if (entry.isDir) {
      if (state.fsTreeExpanded.has(entry.uri)) {
        state.fsTreeExpanded.delete(entry.uri);
        await renderFsTree();
      } else if (state.fsTreeData[entry.uri]) {
        state.fsTreeExpanded.add(entry.uri);
        await renderFsTree();
      } else {
        try {
          const payload = await callConsole(
            `/ov/fs/ls?uri=${encodeURIComponent(entry.uri)}&show_all_hidden=true`,
            { method: "GET" }
          );
          const children = normalizeFsEntries(payload.result, entry.uri);
          children.sort((a, b) => {
            if (a.isDir !== b.isDir) {
              return a.isDir ? -1 : 1;
            }
            return (a.uri || "").localeCompare(b.uri || "");
          });
          state.fsTreeData[entry.uri] = children;
          state.fsTreeExpanded.add(entry.uri);
          await renderFsTree();
        } catch (error) {
          setOutput(error.message);
        }
      }
    } else {
      try {
        await readFilesystemFile(entry);
      } catch (error) {
        setOutput(error.message);
      }
    }
  });

  return item;
}

async function renderFsTreeLevel(container, uri, depth) {
  const entries = state.fsTreeData[uri] || [];
  for (const entry of entries) {
    const item = buildFsTreeItem(entry, depth);
    container.appendChild(item);
    if (entry.isDir && state.fsTreeExpanded.has(entry.uri)) {
      const childContainer = document.createElement("div");
      childContainer.className = "fs-tree-children";
      container.appendChild(childContainer);
      await renderFsTreeLevel(childContainer, entry.uri, depth + 1);
    }
  }
}

async function renderFsTree() {
  elements.fsTree.innerHTML = "";
  await renderFsTreeLevel(elements.fsTree, state.fsCurrentUri, 0);
}

function setFsViewMode(mode) {
  state.fsViewMode = mode;
  elements.fsModeListBtn.classList.toggle("active", mode === "list");
  elements.fsModeTreeBtn.classList.toggle("active", mode === "tree");
  elements.fsModeListBtn.setAttribute("aria-pressed", String(mode === "list"));
  elements.fsModeTreeBtn.setAttribute("aria-pressed", String(mode === "tree"));
  elements.fsTableWrap.hidden = mode === "tree";
  elements.fsTree.hidden = mode === "list";
}

async function loadFilesystem(uri, { pushHistory = false } = {}) {
  const targetUri = normalizeDirUri(uri);
  const payload = await callConsole(
    `/ov/fs/ls?uri=${encodeURIComponent(targetUri)}&show_all_hidden=true`,
    { method: "GET" }
  );

  if (pushHistory && state.fsCurrentUri !== targetUri) {
    state.fsHistory.push(state.fsCurrentUri);
  }

  state.fsCurrentUri = targetUri;
  elements.fsCurrentUri.value = targetUri;

  const rawEntries = normalizeFsEntries(payload.result, targetUri);

  if (state.fsViewMode === "list") {
    const entries = sortFilesystemEntries(rawEntries);
    renderFilesystemEntries(
      elements.fsEntries,
      entries,
      async (entry) => {
        if (entry.isDir) {
          try {
            await loadFilesystem(entry.uri, { pushHistory: true });
          } catch (error) {
            setOutput(error.message);
          }
          return;
        }
        try {
          await readFilesystemFile(entry);
        } catch (error) {
          setOutput(error.message);
        }
      },
      async (entry) => {
        await statFilesystemResource(entry);
      }
    );
  } else {
    rawEntries.sort((a, b) => {
      if (a.isDir !== b.isDir) {
        return a.isDir ? -1 : 1;
      }
      return (a.uri || "").localeCompare(b.uri || "");
    });
    state.fsTreeData[targetUri] = rawEntries;
    await renderFsTree();
  }

  setOutput(payload);
}

async function refreshCapabilities() {
  try {
    const payload = await callConsole("/runtime/capabilities", { method: "GET" });
    state.writeEnabled = Boolean(payload.result?.write_enabled);
    elements.writeBadge.textContent = state.writeEnabled ? "Write Enabled" : "Readonly";
    elements.writeBadge.classList.toggle("write", state.writeEnabled);
    elements.addResourceBtn.disabled = !state.writeEnabled;
    elements.addResourceUploadBtn.disabled = !state.writeEnabled;
    syncWriteControls();
    renderAccountsTable();
    renderUsersTable();
  } catch (error) {
    setOutput(`Failed to load capabilities: ${error.message}`);
  }
}

function bindShellControls() {
  const preferDark = window.matchMedia("(prefers-color-scheme: dark)");

  if (elements.navToggleBtn) {
    elements.navToggleBtn.addEventListener("click", () => {
      setNavCollapsed(!state.navCollapsed);
    });
  }

  if (elements.resultToggleBtn) {
    elements.resultToggleBtn.addEventListener("click", () => {
      setResultCollapsed(!state.resultCollapsed);
    });
  }

  if (elements.clearOutputBtn) {
    elements.clearOutputBtn.addEventListener("click", () => {
      setOutput("Ready.");
    });
  }

  for (const button of elements.themeButtons) {
    button.addEventListener("click", () => {
      applyThemeMode(button.dataset.themeMode || "dark");
    });
  }

  if (elements.content) {
    elements.content.addEventListener("click", () => {
      if (window.matchMedia("(max-width: 900px)").matches && !state.navCollapsed) {
        setNavCollapsed(true);
      }
    });
  }

  const onThemeChange = () => {
    if (state.themeMode === "system") {
      applyThemeMode("system", { persist: false });
    }
  };
  if (typeof preferDark.addEventListener === "function") {
    preferDark.addEventListener("change", onThemeChange);
  } else if (typeof preferDark.addListener === "function") {
    preferDark.addListener(onThemeChange);
  }
}

function initShellState() {
  const storedTheme = readLocalStorage(THEME_MODE_KEY);
  const themeMode = storedTheme === "light" || storedTheme === "system" ? storedTheme : "dark";
  applyThemeMode(themeMode, { persist: false });

  const storedNav = readLocalStorage(NAV_COLLAPSED_KEY);
  const defaultNavCollapsed =
    storedNav === "1" || (storedNav === null && window.matchMedia("(max-width: 900px)").matches);
  setNavCollapsed(defaultNavCollapsed, { persist: false });

  const storedResult = readLocalStorage(RESULT_COLLAPSED_KEY);
  setResultCollapsed(storedResult === "1", { persist: false });
}

function bindTabs() {
  for (const tab of elements.tabs) {
    tab.addEventListener("click", () => {
      const panel = tab.dataset.panel;
      if (!panel) {
        return;
      }
      setActivePanel(panel);
    });
  }
}

function bindConnection() {
  const saveApiKey = () => {
    const value = elements.apiKeyInput.value.trim();
    if (!value) {
      setOutput("API key is empty.");
      return false;
    }

    window.sessionStorage.setItem(SESSION_KEY, value);
    elements.apiKeyInput.value = "";
    updateConnectionHint();
    setOutput("API key saved in browser session storage.");
    return true;
  };

  elements.saveKeyBtn.addEventListener("click", () => {
    saveApiKey();
  });

  elements.apiKeyInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    saveApiKey();
  });

  elements.clearKeyBtn.addEventListener("click", () => {
    window.sessionStorage.removeItem(SESSION_KEY);
    updateConnectionHint();
    setOutput("API key cleared from browser session.");
  });
}

function bindFilesystem() {
  bindFilesystemSort();
  updateFilesystemSortHeaders();

  elements.fsGoBtn.addEventListener("click", async () => {
    try {
      await loadFilesystem(elements.fsCurrentUri.value, { pushHistory: true });
    } catch (error) {
      setOutput(error.message);
    }
  });

  elements.fsRefreshBtn.addEventListener("click", async () => {
    try {
      await loadFilesystem(state.fsCurrentUri);
    } catch (error) {
      setOutput(error.message);
    }
  });

  elements.fsBackBtn.addEventListener("click", async () => {
    if (!state.fsHistory.length) {
      setOutput("No previous directory.");
      return;
    }

    const previous = state.fsHistory.pop();
    try {
      await loadFilesystem(previous);
    } catch (error) {
      setOutput(error.message);
    }
  });

  elements.fsUpBtn.addEventListener("click", async () => {
    const parent = parentUri(state.fsCurrentUri);
    if (parent === state.fsCurrentUri) {
      setOutput("Already at viking:// root.");
      return;
    }

    state.fsHistory.push(state.fsCurrentUri);
    try {
      await loadFilesystem(parent);
    } catch (error) {
      setOutput(error.message);
    }
  });

  elements.fsModeListBtn.addEventListener("click", () => {
    setFsViewMode("list");
    loadFilesystem(state.fsCurrentUri).catch((e) => setOutput(e.message));
  });

  elements.fsModeTreeBtn.addEventListener("click", async () => {
    if (state.fsViewMode === "tree") {
      // Already in tree mode: toggle all collapse ↔ expand (first level)
      if (state.fsTreeExpanded.size > 0) {
        state.fsTreeExpanded.clear();
        await renderFsTree();
      } else {
        const firstLevel = state.fsTreeData[state.fsCurrentUri] || [];
        await Promise.all(
          firstLevel
            .filter((e) => e.isDir && !state.fsTreeData[e.uri])
            .map(async (e) => {
              try {
                const payload = await callConsole(
                  `/ov/fs/ls?uri=${encodeURIComponent(e.uri)}&show_all_hidden=true`,
                  { method: "GET" }
                );
                const children = normalizeFsEntries(payload.result, e.uri);
                children.sort((a, b) => {
                  if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
                  return (a.uri || "").localeCompare(b.uri || "");
                });
                state.fsTreeData[e.uri] = children;
              } catch (_) {}
            })
        );
        for (const entry of firstLevel) {
          if (entry.isDir) state.fsTreeExpanded.add(entry.uri);
        }
        await renderFsTree();
      }
      return;
    }
    setFsViewMode("tree");
    state.fsTreeData = {};
    state.fsTreeExpanded = new Set();
    loadFilesystem(state.fsCurrentUri).catch((e) => setOutput(e.message));
  });
}

function bindFind() {
  elements.findBtn.addEventListener("click", async () => {
    const query = elements.findQuery.value.trim();
    if (!query) {
      setOutput("Query cannot be empty.");
      return;
    }

    try {
      const payload = await callConsole("/ov/search/find", {
        method: "POST",
        body: JSON.stringify({
          query,
          target_uri: elements.findTarget.value.trim(),
          limit: 10,
        }),
      });

      const rows = normalizeFindRows(payload.result);
      renderFindTable(rows);
      setOutput(payload);
    } catch (error) {
      setOutput(error.message);
    }
  });
}

function buildAddResourcePayload() {
  const payload = {
    target: elements.addResourceTarget.value.trim(),
    reason: elements.addResourceReason.value.trim(),
    instruction: elements.addResourceInstruction.value.trim(),
    wait: elements.addResourceWait.checked,
    strict: elements.addResourceStrict.checked,
    directly_upload_media: elements.addResourceUploadMedia.checked,
  };

  const timeoutRaw = elements.addResourceTimeout.value.trim();
  if (timeoutRaw) {
    const timeout = Number.parseFloat(timeoutRaw);
    if (Number.isFinite(timeout) && timeout > 0) {
      payload.timeout = timeout;
    }
  }

  const ignoreDirs = elements.addResourceIgnoreDirs.value.trim();
  if (ignoreDirs) {
    payload.ignore_dirs = ignoreDirs;
  }

  const include = elements.addResourceInclude.value.trim();
  if (include) {
    payload.include = include;
  }

  const exclude = elements.addResourceExclude.value.trim();
  if (exclude) {
    payload.exclude = exclude;
  }

  return payload;
}

function bindAddResource() {
  elements.addResourceBtn.addEventListener("click", async () => {
    if (!state.writeEnabled) {
      setOutput("Write mode is disabled on the server.");
      return;
    }

    const path = elements.addResourcePath.value.trim();
    if (!path) {
      setOutput("Path cannot be empty. Or use Upload & Add.");
      return;
    }

    try {
      const payload = await callConsole("/ov/resources", {
        method: "POST",
        body: JSON.stringify({
          ...buildAddResourcePayload(),
          path,
        }),
      });
      setOutput(payload);
    } catch (error) {
      setOutput(error.message);
    }
  });

  elements.addResourceUploadBtn.addEventListener("click", async () => {
    if (!state.writeEnabled) {
      setOutput("Write mode is disabled on the server.");
      return;
    }

    const file = elements.addResourceFile.files?.[0];
    if (!file) {
      setOutput("Please select a file first.");
      return;
    }

    const formData = new FormData();
    formData.append("file", file);

    try {
      setOutput(`Uploading ${file.name} ...`);
      const uploadPayload = await callConsole("/ov/resources/temp_upload", {
        method: "POST",
        body: formData,
      });
      const tempPath = uploadPayload.result?.temp_path;
      if (!tempPath) {
        throw new Error("Temp upload did not return temp_path.");
      }

      const addPayload = await callConsole("/ov/resources", {
        method: "POST",
        body: JSON.stringify({
          ...buildAddResourcePayload(),
          temp_path: tempPath,
        }),
      });

      setOutput({
        status: "ok",
        result: {
          upload: uploadPayload.result,
          add_resource: addPayload.result,
        },
      });
    } catch (error) {
      setOutput(error.message);
    }
  });
}

function normalizeArrayResult(result, candidateKeys = []) {
  if (Array.isArray(result)) {
    return result;
  }
  if (isRecord(result)) {
    for (const key of candidateKeys) {
      if (Array.isArray(result[key])) {
        return result[key];
      }
    }
  }
  return [];
}

function normalizeTenantAccount(item) {
  if (typeof item === "string") {
    const accountId = item.trim();
    return accountId
      ? {
          accountId,
          userCount: null,
          raw: item,
        }
      : null;
  }

  if (!isRecord(item)) {
    return null;
  }

  const accountIdValue = pickFirstNonEmpty([
    item.account_id,
    item.accountId,
    item.id,
    item.name,
    item.uri,
  ]);
  if (accountIdValue === null) {
    return null;
  }

  return {
    accountId: String(accountIdValue),
    userCount: pickFirstNonEmpty([item.user_count, item.userCount, item.users, item.member_count]),
    raw: item,
  };
}

function normalizeTenantUser(item) {
  if (typeof item === "string") {
    const userId = item.trim();
    return userId ? { userId, role: "", raw: item } : null;
  }

  if (!isRecord(item)) {
    return null;
  }

  const userIdValue = pickFirstNonEmpty([item.user_id, item.userId, item.id, item.name]);
  if (userIdValue === null) {
    return null;
  }

  let role = pickFirstNonEmpty([item.role, item.user_role, item.userRole, item.permission, item.permissions]);
  if (role === null && typeof item.is_admin === "boolean") {
    role = item.is_admin ? "admin" : "member";
  }

  return {
    userId: String(userIdValue),
    role: role === null ? "" : String(role),
    raw: item,
  };
}

function updateTenantCurrentAccountLabel() {
  elements.tenantCurrentAccount.textContent = state.tenantSelectedAccountId
    ? `Account: ${state.tenantSelectedAccountId}`
    : "No account selected";
}

function compareTenantRows(left, right, field) {
  const leftValue = isRecord(left) ? left[field] : undefined;
  const rightValue = isRecord(right) ? right[field] : undefined;
  return compareFindValues(leftValue, rightValue);
}

function sortTenantRows(rows, field, direction) {
  const sorted = [...rows].sort((left, right) => compareTenantRows(left, right, field));
  if (direction === "desc") {
    sorted.reverse();
  }
  return sorted;
}

function applyTenantAccountFilter() {
  const keyword = elements.tenantAccountSearch.value.trim().toLowerCase();
  state.tenantFilteredAccounts = state.tenantAccounts.filter((account) =>
    account.accountId.toLowerCase().includes(keyword)
  );
}

function updateTenantSortButtons(buttons, activeField, direction) {
  for (const button of buttons) {
    const field = button.dataset.tenantAccountSort || button.dataset.tenantUserSort || "";
    const isActive = field === activeField;
    const suffix = !isActive ? "" : direction === "asc" ? " ↑" : " ↓";
    button.textContent = `${field}${suffix}`;
    button.setAttribute("aria-sort", isActive ? (direction === "asc" ? "ascending" : "descending") : "none");
  }
}

function renderAccountsTable() {
  if (!elements.tenantAccountsBody) {
    return;
  }

  elements.tenantAccountsBody.innerHTML = "";
  applyTenantAccountFilter();
  const rows = sortTenantRows(
    state.tenantFilteredAccounts,
    state.tenantAccountSortField,
    state.tenantAccountSortDirection
  );
  updateTenantSortButtons(
    elements.tenantAccountSortBtns,
    state.tenantAccountSortField,
    state.tenantAccountSortDirection
  );

  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 3;
    td.className = "tenant-empty";
    td.textContent = "No accounts";
    tr.appendChild(td);
    elements.tenantAccountsBody.appendChild(tr);
    return;
  }

  for (const account of rows) {
    const tr = document.createElement("tr");
    tr.classList.toggle("tenant-row-selected", account.accountId === state.tenantSelectedAccountId);

    const accountCell = document.createElement("td");
    const accountBtn = document.createElement("button");
    accountBtn.type = "button";
    accountBtn.className = "tenant-account-btn";
    accountBtn.textContent = account.accountId;
    accountBtn.addEventListener("click", async () => {
      state.tenantSelectedAccountId = account.accountId;
      updateTenantCurrentAccountLabel();
      renderAccountsTable();
      try {
        await loadTenantUsers(account.accountId);
      } catch (error) {
        setOutput(error.message);
      }
    });
    accountCell.appendChild(accountBtn);
    tr.appendChild(accountCell);

    const countCell = document.createElement("td");
    countCell.textContent =
      account.userCount === null || account.userCount === undefined || account.userCount === ""
        ? "-"
        : String(account.userCount);
    tr.appendChild(countCell);

    const actionCell = document.createElement("td");
    const actions = document.createElement("div");
    actions.className = "tenant-actions";

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "danger";
    deleteBtn.textContent = "Delete";
    deleteBtn.disabled = !state.writeEnabled;
    deleteBtn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      void executeTenantAction(
        {
          title: "Delete account",
          message: `Delete account "${account.accountId}" and its tenant users?`,
          confirmLabel: `Type ${account.accountId} to confirm`,
          confirmToken: account.accountId,
          actionLabel: "Delete account",
          run: async () =>
            callConsole(`/ov/admin/accounts/${encodeURIComponent(account.accountId)}`, {
              method: "DELETE",
            }),
          afterSuccess: async () => {
            await loadTenantAccounts({ showOutput: false });
          },
        },
        { confirm: true }
      );
    });
    actions.appendChild(deleteBtn);

    actionCell.appendChild(actions);
    tr.appendChild(actionCell);
    elements.tenantAccountsBody.appendChild(tr);
  }
}

function tenantRoleOptions(role) {
  const defaults = ["user", "admin"];
  if (role && !defaults.includes(role)) {
    defaults.unshift(role);
  }
  return defaults;
}

function renderUsersTable() {
  if (!elements.tenantUsersBody) {
    return;
  }

  elements.tenantUsersBody.innerHTML = "";
  updateTenantSortButtons(elements.tenantUserSortBtns, state.tenantUserSortField, state.tenantUserSortDirection);

  if (!state.tenantSelectedAccountId) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 3;
    td.className = "tenant-empty";
    td.textContent = "Select an account to view users";
    tr.appendChild(td);
    elements.tenantUsersBody.appendChild(tr);
    return;
  }

  const rows = sortTenantRows(state.tenantUsers, state.tenantUserSortField, state.tenantUserSortDirection);
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 3;
    td.className = "tenant-empty";
    td.textContent = "No users";
    tr.appendChild(td);
    elements.tenantUsersBody.appendChild(tr);
    return;
  }

  for (const user of rows) {
    const tr = document.createElement("tr");

    const userIdCell = document.createElement("td");
    userIdCell.textContent = user.userId;
    tr.appendChild(userIdCell);

    const roleCell = document.createElement("td");
    roleCell.textContent = user.role || "-";
    tr.appendChild(roleCell);

    const actionCell = document.createElement("td");
    const actions = document.createElement("div");
    actions.className = "tenant-actions";

    const roleSelect = document.createElement("select");
    roleSelect.className = "tenant-role-select";
    for (const optionValue of tenantRoleOptions(user.role)) {
      const option = document.createElement("option");
      option.value = optionValue;
      option.textContent = optionValue;
      option.selected = optionValue === (user.role || "member");
      roleSelect.appendChild(option);
    }
    actions.appendChild(roleSelect);

    const roleBtn = document.createElement("button");
    roleBtn.type = "button";
    roleBtn.textContent = "Update Role";
    roleBtn.disabled = !state.writeEnabled;
    roleBtn.addEventListener("click", () => {
      void executeTenantAction({
        title: "Update user role",
        message: `Set role for "${user.userId}" under "${state.tenantSelectedAccountId}" to "${roleSelect.value}".`,
        confirmLabel: `Type ${state.tenantSelectedAccountId}/${user.userId} to confirm`,
        confirmToken: `${state.tenantSelectedAccountId}/${user.userId}`,
        actionLabel: "Save role",
        run: async () =>
          callConsole(
            `/ov/admin/accounts/${encodeURIComponent(state.tenantSelectedAccountId)}/users/${encodeURIComponent(
              user.userId
            )}/role`,
            {
              method: "PUT",
              body: JSON.stringify({ role: roleSelect.value }),
            }
          ),
        afterSuccess: async () => {
          await loadTenantUsers(state.tenantSelectedAccountId, { showOutput: false });
        },
      });
    });
    actions.appendChild(roleBtn);

    const keyBtn = document.createElement("button");
    keyBtn.type = "button";
    keyBtn.textContent = "Reset API Key";
    keyBtn.disabled = !state.writeEnabled;
    keyBtn.addEventListener("click", () => {
      void executeTenantAction({
        title: "Reset API key",
        message: `Generate a new API key for "${user.userId}" under "${state.tenantSelectedAccountId}".`,
        confirmLabel: `Type ${state.tenantSelectedAccountId}/${user.userId} to confirm`,
        confirmToken: `${state.tenantSelectedAccountId}/${user.userId}`,
        actionLabel: "Reset key",
        run: async () =>
          callConsole(
            `/ov/admin/accounts/${encodeURIComponent(state.tenantSelectedAccountId)}/users/${encodeURIComponent(
              user.userId
            )}/key`,
            { method: "POST", body: JSON.stringify({}) }
          ),
      });
    });
    actions.appendChild(keyBtn);

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "danger";
    deleteBtn.textContent = "Remove";
    deleteBtn.disabled = !state.writeEnabled;
    deleteBtn.addEventListener("click", () => {
      void executeTenantAction(
        {
          title: "Remove user",
          message: `Remove "${user.userId}" from account "${state.tenantSelectedAccountId}".`,
          confirmLabel: `Type ${state.tenantSelectedAccountId}/${user.userId} to confirm`,
          confirmToken: `${state.tenantSelectedAccountId}/${user.userId}`,
          actionLabel: "Remove user",
          run: async () =>
            callConsole(
              `/ov/admin/accounts/${encodeURIComponent(state.tenantSelectedAccountId)}/users/${encodeURIComponent(
                user.userId
              )}`,
              { method: "DELETE" }
            ),
          afterSuccess: async () => {
            await loadTenantUsers(state.tenantSelectedAccountId, { showOutput: false });
          },
        },
        { confirm: true }
      );
    });
    actions.appendChild(deleteBtn);

    actionCell.appendChild(actions);
    tr.appendChild(actionCell);
    elements.tenantUsersBody.appendChild(tr);
  }
}

async function loadTenantUsers(accountId, { showOutput = true } = {}) {
  if (!accountId) {
    state.tenantUsers = [];
    updateTenantCurrentAccountLabel();
    renderUsersTable();
    return null;
  }

  const payload = await callConsole(`/ov/admin/accounts/${encodeURIComponent(accountId)}/users`, {
    method: "GET",
  });
  const normalizedUsers = normalizeArrayResult(payload.result, ["users", "items", "results"])
    .map(normalizeTenantUser)
    .filter(Boolean);
  state.tenantSelectedAccountId = accountId;
  state.tenantUsers = normalizedUsers;
  updateTenantCurrentAccountLabel();
  renderUsersTable();
  if (showOutput) {
    setOutput(payload);
  }
  return payload;
}

async function loadTenantAccounts({ showOutput = true } = {}) {
  const payload = await callConsole("/ov/admin/accounts", { method: "GET" });
  const normalizedAccounts = normalizeArrayResult(payload.result, ["accounts", "items", "results"])
    .map(normalizeTenantAccount)
    .filter(Boolean);
  state.tenantAccounts = normalizedAccounts;
  state.tenantAccountsLoaded = true;

  const hasSelected = state.tenantSelectedAccountId
    ? normalizedAccounts.some((account) => account.accountId === state.tenantSelectedAccountId)
    : false;
  if (!hasSelected) {
    state.tenantSelectedAccountId = normalizedAccounts[0]?.accountId || "";
  }

  renderAccountsTable();
  if (state.tenantSelectedAccountId) {
    await loadTenantUsers(state.tenantSelectedAccountId, { showOutput: false });
  } else {
    state.tenantUsers = [];
    updateTenantCurrentAccountLabel();
    renderUsersTable();
  }
  if (showOutput) {
    setOutput(payload);
  }
  return payload;
}

async function ensureTenantsLoaded() {
  if (!state.tenantAccountsLoaded) {
    await loadTenantAccounts({ showOutput: false });
  }
}

function closeTenantConfirmModal() {
  elements.tenantConfirmModal.hidden = true;
  elements.tenantConfirmInput.value = "";
  elements.tenantConfirmError.hidden = true;
  elements.tenantConfirmError.textContent = "";
  state.tenantConfirmRequest = null;
}

function updateTenantConfirmState() {
  const request = state.tenantConfirmRequest;
  if (!request) {
    return;
  }
  const expected = request.confirmToken || "";
  const value = elements.tenantConfirmInput.value.trim();
  const valid = !expected || value === expected;
  elements.tenantConfirmActionBtn.disabled = !valid;
  elements.tenantConfirmError.hidden = true;
  elements.tenantConfirmError.textContent = "";
}

function openTenantConfirmModal(request) {
  state.tenantConfirmRequest = request;
  elements.tenantConfirmTitle.textContent = request.title;
  elements.tenantConfirmMessage.textContent = request.message;
  elements.tenantConfirmLabel.textContent = request.confirmLabel || "Type to confirm";
  elements.tenantConfirmActionBtn.textContent = request.actionLabel || "Confirm";
  elements.tenantConfirmInput.value = "";
  elements.tenantConfirmActionBtn.disabled = true;
  elements.tenantConfirmError.hidden = true;
  elements.tenantConfirmError.textContent = "";
  elements.tenantConfirmModal.hidden = false;
  updateTenantConfirmState();
  elements.tenantConfirmInput.focus();
}

async function performTenantAction(request) {
  const payload = await request.run();
  if (request.afterSuccess) {
    await request.afterSuccess(payload);
  }
  setOutput(payload);
}

async function executeTenantAction(request, { confirm = false } = {}) {
  if (!state.writeEnabled) {
    setOutput("Write mode is disabled on the server.");
    return;
  }

  if (confirm) {
    openTenantConfirmModal(request);
    return;
  }

  try {
    await performTenantAction(request);
  } catch (error) {
    setOutput(error.message);
  }
}

function bindTenantSortButtons() {
  for (const button of elements.tenantAccountSortBtns) {
    button.addEventListener("click", () => {
      const field = button.dataset.tenantAccountSort;
      if (!field) {
        return;
      }
      if (state.tenantAccountSortField === field) {
        state.tenantAccountSortDirection = state.tenantAccountSortDirection === "asc" ? "desc" : "asc";
      } else {
        state.tenantAccountSortField = field;
        state.tenantAccountSortDirection = "asc";
      }
      renderAccountsTable();
    });
  }

  for (const button of elements.tenantUserSortBtns) {
    button.addEventListener("click", () => {
      const field = button.dataset.tenantUserSort;
      if (!field) {
        return;
      }
      if (state.tenantUserSortField === field) {
        state.tenantUserSortDirection = state.tenantUserSortDirection === "asc" ? "desc" : "asc";
      } else {
        state.tenantUserSortField = field;
        state.tenantUserSortDirection = "asc";
      }
      renderUsersTable();
    });
  }
}

function bindAddMemory() {
  elements.addMemoryBtn.addEventListener("click", async () => {
    if (!state.writeEnabled) {
      setOutput("Write mode is disabled on the server.");
      return;
    }

    const text = elements.addMemoryInput.value.trim();
    if (!text) {
      setOutput("Please enter content to add as memory.");
      return;
    }

    let messages;
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed)) {
        messages = parsed;
      } else {
        messages = [{ role: "user", content: text }];
      }
    } catch (_) {
      messages = [{ role: "user", content: text }];
    }

    try {
      setOutput("Creating session...");
      const sessionPayload = await callConsole("/ov/sessions", {
        method: "POST",
        body: JSON.stringify({}),
      });
      const sessionId = sessionPayload.result?.session_id;
      if (!sessionId) {
        throw new Error("Failed to create session: no session_id returned.");
      }

      for (const msg of messages) {
        await callConsole(`/ov/sessions/${sessionId}/messages`, {
          method: "POST",
          body: JSON.stringify(msg),
        });
      }

      setOutput("Committing session...");
      const commitPayload = await callConsole(`/ov/sessions/${sessionId}/commit`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      setOutput(commitPayload);
    } catch (error) {
      setOutput({ error: error.message });
    }
  });
}

function bindTenants() {
  bindTenantSortButtons();
  renderAccountsTable();
  renderUsersTable();
  updateTenantCurrentAccountLabel();

  elements.tenantAccountSearch.addEventListener("input", () => {
    renderAccountsTable();
  });

  elements.tenantRefreshAccountsBtn.addEventListener("click", async () => {
    try {
      await loadTenantAccounts();
    } catch (error) {
      setOutput(error.message);
    }
  });

  elements.tenantCreateAccountBtn.addEventListener("click", async () => {
    const accountId = elements.tenantCreateAccountId.value.trim();
    const adminUserId = elements.tenantCreateAdminUserId.value.trim();
    if (!accountId || !adminUserId) {
      setOutput("Please input account_id and first admin user_id.");
      return;
    }

    await executeTenantAction({
      title: "Create account",
      message: `Create account "${accountId}" with initial admin "${adminUserId}".`,
      confirmLabel: `Type ${accountId} to confirm`,
      confirmToken: accountId,
      actionLabel: "Create account",
      run: async () =>
        callConsole("/ov/admin/accounts", {
          method: "POST",
          body: JSON.stringify({ account_id: accountId, admin_user_id: adminUserId }),
        }),
      afterSuccess: async () => {
        elements.tenantCreateAccountId.value = "";
        await loadTenantAccounts({ showOutput: false });
      },
    });
  });

  elements.tenantAddUserBtn.addEventListener("click", async () => {
    const accountId = state.tenantSelectedAccountId;
    const userId = elements.tenantAddUserId.value.trim();
    const role = elements.tenantAddUserRole.value;
    if (!accountId) {
      setOutput("Select an account before adding users.");
      return;
    }
    if (!userId) {
      setOutput("Please input new user_id.");
      return;
    }

    await executeTenantAction({
      title: "Add user",
      message: `Add user "${userId}" to account "${accountId}" with role "${role}".`,
      confirmLabel: `Type ${accountId}/${userId} to confirm`,
      confirmToken: `${accountId}/${userId}`,
      actionLabel: "Add user",
      run: async () =>
        callConsole(`/ov/admin/accounts/${encodeURIComponent(accountId)}/users`, {
          method: "POST",
          body: JSON.stringify({ user_id: userId, role }),
        }),
      afterSuccess: async () => {
        elements.tenantAddUserId.value = "";
        await loadTenantUsers(accountId, { showOutput: false });
      },
    });
  });

  elements.tenantConfirmInput.addEventListener("input", () => {
    updateTenantConfirmState();
  });

  elements.tenantConfirmCancelBtn.addEventListener("click", () => {
    closeTenantConfirmModal();
  });

  elements.tenantConfirmModal.addEventListener("click", (event) => {
    if (event.target === elements.tenantConfirmModal) {
      closeTenantConfirmModal();
    }
  });

  elements.tenantConfirmActionBtn.addEventListener("click", async () => {
    const request = state.tenantConfirmRequest;
    if (!request) {
      return;
    }

    const expected = request.confirmToken || "";
    const typed = elements.tenantConfirmInput.value.trim();
    if (expected && typed !== expected) {
      elements.tenantConfirmError.hidden = false;
      elements.tenantConfirmError.textContent = "Confirmation text mismatch.";
      return;
    }

    elements.tenantConfirmActionBtn.disabled = true;
    try {
      await performTenantAction(request);
      closeTenantConfirmModal();
    } catch (error) {
      closeTenantConfirmModal();
      setOutput(error.message);
    }
  });
}

function bindMonitor() {
  elements.systemBtn.addEventListener("click", async () => {
    try {
      const payload = await callConsole("/ov/system/status", { method: "GET" });
      const rows = Object.entries(payload.result || {}).map(([key, value]) => ({
        label: `${key}: ${typeof value === "string" ? value : JSON.stringify(value)}`,
      }));
      renderList(elements.monitorResults, rows);
      setOutput(payload);
    } catch (error) {
      setOutput(error.message);
    }
  });

  elements.observerBtn.addEventListener("click", async () => {
    try {
      const payload = await callConsole("/ov/observer/system", { method: "GET" });
      const rows = Object.entries(payload.result?.components || {}).map(([name, value]) => ({
        label: `${name}: ${value?.status || JSON.stringify(value)}`,
      }));
      renderList(elements.monitorResults, rows);
      setOutput(payload);
    } catch (error) {
      setOutput(error.message);
    }
  });
}

async function init() {
  initShellState();
  bindShellControls();
  initResizablePanes();
  initFsColumnResize();
  bindTabs();
  bindConnection();
  bindFilesystem();
  bindFind();
  renderFindTable([]);
  bindAddResource();
  bindAddMemory();
  bindTenants();
  bindMonitor();
  updateConnectionHint();
  setActivePanel(state.activePanel);
  await refreshCapabilities();

  try {
    await loadFilesystem("viking://");
  } catch (error) {
    setOutput(error.message);
  }
}

init();
