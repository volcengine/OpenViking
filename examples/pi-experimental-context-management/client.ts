import type { OVConfig } from "./config.js";

// --- OV API Response Shapes ---
// All OV responses wrap in: { status: "ok"|"error", result: T, error?: {...}, ... }
// This client normalizes to { ok, result } internally.

export interface OVSearchResult {
  uri: string;
  context_type: string;   // "memory" | "resource" | "skill"
  score: number;
  abstract: string;
  overview: string | null;
  level: number;          // 0=L0, 1=L1, 2=L2
  category: string;
  match_reason: string;
}

export interface OVDirEntry {
  uri: string;
  name: string;
  isDir: boolean;
  size: number;
  mode: number;
  modTime: string;
  abstract: string;
}

export interface OVStatInfo {
  name: string;
  size: number;
  mode: number;
  modTime: string;
  isDir: boolean;
  isLocked: boolean;
  uri?: string;
  count?: number;         // directories only
}

export interface OVSessionMeta {
  session_id: string;
  message_count: number;
  total_message_count?: number;
  commit_count: number;
  pending_tokens?: number;
  memories_extracted?: Record<string, number>;
  last_commit_at?: string;
  /** Canonical session root, e.g. `viking://user/default/sessions/pi-abc`. */
  uri?: string;
}

export interface OVSessionContext {
  latest_archive_overview: string | null;
  pre_archive_abstracts: any[];
  messages: any[];
  estimatedTokens: number;
  stats: {
    totalArchives: number;
    includedArchives: number;
    droppedArchives: number;
    failedArchives: number;
    activeTokens: number;
    archiveTokens: number;
  };
}

export interface OVCommitResult {
  /**
   * `accepted` = phase 1 wrote `history/archive_NNN/messages.jsonl` and a
   * background task is generating the Working Memory; `skipped` = nothing to
   * archive (see `reason`, e.g. `no_messages`).
   */
  status?: "accepted" | "skipped" | string;
  /** Number of messages archived (server also uses a boolean in some builds). */
  archived?: number | boolean;
  reason?: string;
  task_id?: string;
  /** `null` on a `skipped` commit — the server sends the key either way. */
  archive_uri?: string | null;
  trace_id?: string;
}

/** One message of `history/archive_NNN/messages.jsonl`. */
export interface OVArchiveMessage {
  id: string;
  role: string;
  /**
   * Parts rendered to one string: `text` parts verbatim, `tool` parts as
   * `[tool <name>] <input> <output>`, `context` parts as `[context <uri>]`.
   * A part the renderer does not understand never silently disappears.
   */
  text: string;
  parts: any[];
  created_at: string;
}

/** One `history/archive_NNN` directory of a session. */
export interface OVArchiveEntry {
  archiveId: string;
  uri: string;
  modTime: string;
  abstract: string;
}

/** One grep hit inside a session archive. */
export interface OVArchiveGrepMatch {
  archiveId: string;
  /** File inside the archive, e.g. `messages.jsonl` or `.overview.md`. */
  file: string;
  line: number;
  content: string;
}

export interface OVTaskStatus {
  status: string;
  stage?: string;
  error?: string;
}

export interface OVCommitResponse {
  result: OVCommitResult | null;
  traceId?: string;
  error?: any;
  status?: number;
}

export interface OVResponse<T> {
  ok: boolean;
  result: T | null;
  error?: any;
  status?: number;
  traceId?: string;
}

export class OVClient {
  private baseUrl: string;
  private apiKey: string;
  private account: string;
  private user: string;
  private peerId: string;
  connected: boolean = false;

  private resolvedSpaces: Map<string, string> = new Map();
  /** Canonical session roots, keyed by session id. Only successes are cached. */
  private sessionRoots: Map<string, string> = new Map();

  private static RESERVED_USER = new Set(["memories"]);
  private static RESERVED_AGENT = new Set(["memories", "skills", "instructions", "workspaces"]);

  /** Read-only access to config (for value access across modules). */
  readonly cfg: OVConfig;

  constructor(config: OVConfig) {
    this.cfg = config;
    this.baseUrl = config.endpoint.replace(/\/+$/, "");
    this.apiKey = config.apiKey;
    this.account = config.account;
    this.user = config.user;
    this.peerId = config.peerId;
  }

  private headers(): Record<string, string> {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    if (this.apiKey) h["Authorization"] = `Bearer ${this.apiKey}`;
    if (this.account) h["X-OpenViking-Account"] = this.account;
    if (this.user) h["X-OpenViking-User"] = this.user;
    if (this.peerId) h["X-OpenViking-Actor-Peer"] = this.peerId;
    if (this.cfg.userAgent) h["User-Agent"] = this.cfg.userAgent;
    return h;
  }

  /** Core fetch wrapper. Returns { ok, result } after parsing OV's { status, result } envelope. */
  async fetchJSON<T>(path: string, init?: RequestInit, timeoutMs = 10000): Promise<OVResponse<T>> {
    const controller = new AbortController();
    // Cleared in `finally`: when fetch rejects the timer would otherwise keep
    // the event loop alive for the full timeout.
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const resp = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        headers: { ...this.headers(), ...(init?.headers as Record<string, string> || {}) },
        signal: controller.signal,
      });
      const body = await resp.json().catch(() => ({}));
      const traceId = body?.result?.trace_id || body?.error?.trace_id || body?.trace_id || undefined;
      if (!resp.ok || body.status === "error") {
        return {
          ok: false,
          result: null,
          status: resp.status,
          error: body.error || { message: `HTTP ${resp.status}` },
          traceId,
        };
      }
      return { ok: true, result: (body.result ?? body) as T, traceId };
    } catch (err: any) {
      return { ok: false, result: null, status: 0, error: { message: err?.message || String(err) } };
    } finally {
      clearTimeout(timer);
    }
  }

  // ========== Health ==========

  async health(): Promise<boolean> {
    const res = await this.fetchJSON<any>("/health", undefined, 5000);
    this.connected = res.ok;
    return res.ok;
  }

  // ========== Sessions ==========

  /** POST /api/v1/sessions — create or reuse session */
  async createSession(sessionId: string): Promise<boolean> {
    const res = await this.fetchJSON<any>("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId }),
    });
    return res.ok;
  }

  /** GET /api/v1/sessions/{id} — session metadata */
  async getSession(sessionId: string, autoCreate = false): Promise<OVSessionMeta | null> {
    const q = autoCreate ? "?auto_create=true" : "";
    const res = await this.fetchJSON<OVSessionMeta>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}${q}`,
      undefined, 5000,
    );
    return res.ok ? res.result : null;
  }

  /** GET /api/v1/sessions/{id}/context — assembled context with archive overview */
  async getSessionContext(sessionId: string, tokenBudget = 128000): Promise<OVSessionContext | null> {
    const res = await this.fetchJSON<OVSessionContext>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/context?token_budget=${tokenBudget}`,
      undefined, 10000,
    );
    return res.ok ? res.result : null;
  }

  /** POST /api/v1/sessions/{id}/messages — add a message (simple text mode) */
  async addMessage(sessionId: string, role: string, content: string): Promise<boolean> {
    const res = await this.fetchJSON<any>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
      { method: "POST", body: JSON.stringify({ role, content }) },
      10000,
    );
    return res.ok;
  }

  /** POST /api/v1/sessions/{id}/messages — add a message with parts */
  async addMessageParts(sessionId: string, role: string, parts: any[]): Promise<boolean> {
    const res = await this.fetchJSON<any>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
      { method: "POST", body: JSON.stringify({ role, parts }) },
      10000,
    );
    return res.ok;
  }

  async addMessagePayload(sessionId: string, payload: any): Promise<boolean> {
    const res = await this.fetchJSON<any>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
      { method: "POST", body: JSON.stringify(payload) },
      10000,
    );
    return res.ok;
  }

  /** POST /api/v1/sessions/{id}/commit — commit session for archiving + extraction */
  async commitSessionResponse(
    sessionId: string,
    keepRecentCount = this.cfg.commitKeepRecentCount,
  ): Promise<OVCommitResponse> {
    const res = await this.fetchJSON<OVCommitResult>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`,
      { method: "POST", body: JSON.stringify({ keep_recent_count: keepRecentCount }) },
      30000,
    );
    if (res.ok && res.result && !res.result.trace_id && res.traceId) {
      res.result.trace_id = res.traceId;
    }
    return {
      result: res.ok ? res.result : null,
      traceId: res.traceId,
      error: res.error,
      status: res.status,
    };
  }

  async commitSession(
    sessionId: string,
    keepRecentCount = this.cfg.commitKeepRecentCount,
  ): Promise<OVCommitResult | null> {
    return (await this.commitSessionResponse(sessionId, keepRecentCount)).result;
  }

  /** DELETE /api/v1/sessions/{id} */
  async deleteSession(sessionId: string): Promise<boolean> {
    const res = await this.fetchJSON<any>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}`,
      { method: "DELETE" },
      10000,
    );
    return res.ok;
  }

  // ========== Session archives ==========
  //
  // `GET /api/v1/sessions/{id}/archives/{archive_id}` is blocked (403 ApiBlocked)
  // on the target gateway, so everything here goes through the generic
  // filesystem / content / grep routes against the session root URI.
  //
  // Every method in this section is failure-tolerant by contract: it returns
  // null (single value) or [] (list) instead of throwing, so callers can treat
  // "OpenViking did not answer" the same as "nothing there".

  /**
   * Session root URI, e.g. `viking://user/default/sessions/pi-abc`.
   *
   * Reads the canonical `uri` from `GET /api/v1/sessions/{id}` and caches it.
   * When the server does not answer (or answers without a `viking://` uri) this
   * falls back to the legacy alias `viking://session/{id}`, which the target
   * server still accepts. The fallback is deliberately NOT cached so a later
   * call can still pick up the canonical URI. Note the `viking://~` alias is
   * rejected by the server ("Invalid scope '~'") and is never produced here.
   */
  async sessionRootUri(sessionId: string): Promise<string> {
    const id = String(sessionId ?? "");
    const cached = this.sessionRoots.get(id);
    if (cached) return cached;
    try {
      const meta = await this.getSession(id);
      const uri = typeof meta?.uri === "string" ? stripTrailingSlash(meta.uri.trim()) : "";
      if (uri.startsWith("viking://")) {
        this.sessionRoots.set(id, uri);
        return uri;
      }
    } catch {
      // fall through to the legacy alias
    }
    return `viking://session/${id}`;
  }

  /** Strip `/history/archive_NNN` off a commit's `archive_uri` to get the session root. */
  static archiveUriToRoot(archiveUri: string): string {
    return archiveUriToRoot(archiveUri);
  }

  /** Instance alias of {@link OVClient.archiveUriToRoot}. */
  archiveUriToRoot(archiveUri: string): string {
    return archiveUriToRoot(archiveUri);
  }

  /**
   * Working Memory of one archive: `GET /content/read?uri=<archive>/.overview.md`.
   *
   * Returns null while commit phase 2 is still running (the server answers 404
   * NOT_FOUND until then) and on any other failure. The server stores this
   * sidecar as OKF Markdown — `---\n<yaml>\n---\n\n<body>\n` — and
   * `content/read` only strips that for memory URIs, not for session archives,
   * so the frontmatter is removed here and callers get the Working Memory body.
   *
   * A file that is present but has no body counts as "not ready" (null), so a
   * caller polling for the Working Memory cannot be fooled by an empty write.
   */
  async readArchiveOverview(archiveUri: string): Promise<string | null> {
    const base = stripTrailingSlash(String(archiveUri ?? "").trim());
    if (!base) return null;
    const res = await this.fetchJSON<string>(
      `/api/v1/content/read?uri=${encodeURIComponent(`${base}/.overview.md`)}`,
      undefined, 15000,
    );
    if (!res.ok || typeof res.result !== "string") return null;
    const body = stripFrontmatter(res.result);
    return body.trim() ? body : null;
  }

  /**
   * Messages of one archive: `GET /content/read?uri=<archive>/messages.jsonl`.
   *
   * Readable right after commit phase 1. A line that is malformed or blank
   * becomes a placeholder item rather than failing the read or shifting the
   * ones after it. Returns null when the file cannot be read at all.
   */
  async readArchiveMessages(archiveUri: string): Promise<OVArchiveMessage[] | null> {
    const base = stripTrailingSlash(String(archiveUri ?? "").trim());
    if (!base) return null;
    const res = await this.fetchJSON<string>(
      `/api/v1/content/read?uri=${encodeURIComponent(`${base}/messages.jsonl`)}`,
      undefined, 15000,
    );
    if (!res.ok || typeof res.result !== "string") return null;

    // Every line of the file has to produce exactly one item, because
    // `search_contents` turns a grep hit's line number into an item index
    // (`line - 1`). The only thing dropped is the empty string left behind by a
    // trailing final newline, which is not a line of its own.
    const lines = res.result.split("\n");
    if (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();

    const out: OVArchiveMessage[] = [];
    for (const rawLine of lines) {
      const line = rawLine.trim();
      let obj: any = null;
      if (line) {
        try {
          obj = JSON.parse(line);
        } catch {
          obj = null; // a truncated or non-JSON line must not lose the rest
        }
      }
      if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
        out.push({
          id: "",
          role: line ? "unreadable" : "blank",
          text: line ? "(unreadable archive line)" : "(blank archive line)",
          parts: [],
          created_at: "",
        });
        continue;
      }
      const parts = Array.isArray(obj.parts) ? obj.parts : [];
      out.push({
        id: obj.id == null ? "" : String(obj.id),
        role: obj.role == null ? "" : String(obj.role),
        text: renderMessageText(parts, obj.content),
        parts,
        created_at: obj.created_at == null ? "" : String(obj.created_at),
      });
    }
    return out;
  }

  /**
   * Archive directories of a session, newest first.
   * `GET /fs/ls?uri=<root>/history&sort_by=name&sort_order=desc`.
   *
   * Returns `null` when the listing request itself failed (401/403/5xx/timeout)
   * and `[]` only when the session genuinely has no archives. The two must stay
   * distinguishable: `history` tells the model to trust an empty history and
   * not ask the user, which would be exactly wrong for an unreachable server.
   */
  async listSessionArchives(sessionId: string): Promise<OVArchiveEntry[] | null> {
    const root = await this.sessionRootUri(sessionId);
    const res = await this.fetchJSON<any[]>(
      `/api/v1/fs/ls?uri=${encodeURIComponent(`${root}/history`)}&sort_by=name&sort_order=desc`,
      undefined, 10000,
    );
    // A missing history directory is an empty history, not a failure.
    if (!res.ok) return isNotFound(res) ? [] : null;
    if (!Array.isArray(res.result)) return null;

    const rows: OVArchiveEntry[] = [];
    for (const e of res.result) {
      // The agent-format listing has no `name`, so the URI basename leads.
      const uri = stripTrailingSlash(typeof e?.uri === "string" ? e.uri : "");
      const archiveId = uriBasename(uri) || (typeof e?.name === "string" ? e.name : "");
      if (!ARCHIVE_ID_RE.test(archiveId)) continue;
      if (e?.isDir === false) continue; // an archive is always a directory
      rows.push({
        archiveId,
        uri,
        modTime: typeof e?.modTime === "string" ? e.modTime : "",
        abstract: typeof e?.abstract === "string" ? e.abstract : "",
      });
    }
    // Do not rely on the server honouring sort_order: archive ids are numbered,
    // so order them here as well.
    rows.sort((a, b) => archiveIndex(b.archiveId) - archiveIndex(a.archiveId));
    return rows;
  }

  /**
   * `POST /search/grep` over `<root>/history` (or one archive).
   *
   * `pattern` is a REGULAR EXPRESSION by default; pass `{ literal: true }` to
   * search for the text verbatim (the pattern is regex-escaped then).
   */
  async grepSessionArchives(
    sessionId: string,
    pattern: string,
    opts?: { archiveId?: string; caseInsensitive?: boolean; nodeLimit?: number; literal?: boolean },
  ): Promise<OVArchiveGrepMatch[]> {
    const raw = String(pattern ?? "");
    if (!raw) return [];
    const root = await this.sessionRootUri(sessionId);
    const archiveId = opts?.archiveId && ARCHIVE_ID_RE.test(opts.archiveId) ? opts.archiveId : "";
    const uri = archiveId ? `${root}/history/${archiveId}` : `${root}/history`;
    const nodeLimit = Number.isFinite(opts?.nodeLimit as number)
      ? Math.max(1, Math.min(4096, Math.floor(opts!.nodeLimit as number)))
      : 256;

    const res = await this.fetchJSON<any>("/api/v1/search/grep", {
      method: "POST",
      body: JSON.stringify({
        uri,
        pattern: opts?.literal ? escapeRegExp(raw) : raw,
        case_insensitive: opts?.caseInsensitive !== false,
        node_limit: nodeLimit,
      }),
    }, 15000);
    if (!res.ok || !res.result) return [];

    const matches = Array.isArray(res.result?.matches)
      ? res.result.matches
      : Array.isArray(res.result) ? res.result : [];
    const out: OVArchiveGrepMatch[] = [];
    for (const m of matches) {
      if (!m || typeof m !== "object") continue;
      const matchUri = stripTrailingSlash(typeof m.uri === "string" ? m.uri : "");
      const line = Number(m.line);
      out.push({
        archiveId: archiveIdFromUri(matchUri) || archiveId,
        file: archiveFileFromUri(matchUri),
        line: Number.isFinite(line) ? line : 0,
        content: typeof m.content === "string" ? m.content : "",
      });
    }
    return out;
  }

  /** `GET /api/v1/tasks/{id}` — commit phase 2 progress (`pending|running|completed|failed`). */
  async getTask(taskId: string): Promise<OVTaskStatus | null> {
    const id = String(taskId ?? "").trim();
    if (!id) return null;
    const res = await this.fetchJSON<any>(
      `/api/v1/tasks/${encodeURIComponent(id)}`,
      undefined, 10000,
    );
    if (!res.ok || !res.result || typeof res.result !== "object") return null;
    const r = res.result as any;
    const out: OVTaskStatus = { status: String(r.status ?? r.state ?? "") };
    if (typeof r.stage === "string" && r.stage) out.stage = r.stage;
    const err = r.error ?? r.error_message ?? r.message;
    if (err) out.error = typeof err === "string" ? err : safeStringify(err);
    return out;
  }

  // ========== Search ==========

  /** POST /api/v1/search/find — basic vector search */
  async find(
    query: string,
    opts?: { targetUri?: string; topK?: number; scoreThreshold?: number },
  ): Promise<OVSearchResult[]> {
    const body: Record<string, unknown> = { query };
    if (opts?.targetUri) body.target_uri = opts.targetUri;
    if (opts?.topK) body.limit = opts.topK;
    if (opts?.scoreThreshold) body.score_threshold = opts.scoreThreshold;

    const res = await this.fetchJSON<any>("/api/v1/search/find", {
      method: "POST", body: JSON.stringify(body),
    }, 10000);
    if (!res.ok || !res.result) return [];

    // OV returns { memories: [...], resources: [...], skills: [...], total }
    const all: OVSearchResult[] = [];
    for (const bucket of ["memories", "resources", "skills"]) {
      const items = res.result[bucket];
      if (Array.isArray(items)) {
        for (const m of items) {
          all.push({
            uri: m.uri ?? "",
            context_type: m.context_type ?? (bucket === "memories" ? "memory" : bucket === "skills" ? "skill" : "resource"),
            score: m.score ?? 0,
            abstract: m.abstract ?? "",
            overview: m.overview ?? null,
            level: m.level ?? 0,
            category: m.category ?? "",
            match_reason: m.match_reason ?? "",
          });
        }
      }
    }
    return all;
  }

  // ========== Content ==========

  /** GET /api/v1/content/abstract — L0 summary */
  async abstract(uri: string): Promise<string | null> {
    const res = await this.fetchJSON<string>(
      `/api/v1/content/abstract?uri=${encodeURIComponent(uri)}`,
      undefined, 10000,
    );
    return res.ok ? res.result : null;
  }

  /** GET /api/v1/content/overview — L1 overview (directories only) */
  async overview(uri: string): Promise<string | null> {
    const res = await this.fetchJSON<string>(
      `/api/v1/content/overview?uri=${encodeURIComponent(uri)}`,
      undefined, 10000,
    );
    return res.ok ? res.result : null;
  }

  /** GET /api/v1/content/read — L2 full content (files only) */
  async readContent(uri: string): Promise<string | null> {
    const res = await this.fetchJSON<string>(
      `/api/v1/content/read?uri=${encodeURIComponent(uri)}`,
      undefined, 10000,
    );
    return res.ok ? res.result : null;
  }

  // ========== Filesystem ==========

  /** GET /api/v1/fs/ls — list directory */
  async ls(uri: string): Promise<OVDirEntry[]> {
    const res = await this.fetchJSON<any[]>(
      `/api/v1/fs/ls?uri=${encodeURIComponent(uri)}`,
      undefined, 10000,
    );
    if (!res.ok || !Array.isArray(res.result)) return [];
    return res.result.map(e => ({
      uri: e.uri ?? "",
      name: e.name ?? uriBasename(e.uri ?? ""),
      isDir: e.isDir ?? false,
      size: e.size ?? 0,
      mode: e.mode ?? 0,
      modTime: e.modTime ?? "",
      abstract: e.abstract ?? "",
    }));
  }

  /** GET /api/v1/fs/stat — file/directory metadata */
  async stat(uri: string): Promise<OVStatInfo | null> {
    const res = await this.fetchJSON<OVStatInfo>(
      `/api/v1/fs/stat?uri=${encodeURIComponent(uri)}`,
      undefined, 10000,
    );
    return res.ok ? res.result : null;
  }

  /** DELETE /api/v1/fs — remove file or directory */
  async delete(uri: string, recursive = false): Promise<boolean> {
    const res = await this.fetchJSON<any>(
      `/api/v1/fs?uri=${encodeURIComponent(uri)}&recursive=${recursive}`,
      { method: "DELETE" },
      10000,
    );
    return res.ok;
  }

  // ========== Resources ==========

  /** POST /api/v1/resources — ingest a URL or file path */
  async addResource(
    path: string, opts?: { to?: string },
  ): Promise<{ root_uri: string } | null> {
    const body: Record<string, unknown> = { path };
    if (opts?.to) body.to = opts.to;
    const res = await this.fetchJSON<{ root_uri: string }>(
      "/api/v1/resources",
      { method: "POST", body: JSON.stringify(body) },
      30000,
    );
    return res.ok ? res.result : null;
  }

  // ========== URI Space Resolution ==========

  async resolveScopeSpace(scope: "user" | "agent"): Promise<string> {
    const cached = this.resolvedSpaces.get(scope);
    if (cached) return cached;

    // Probe system status for user identity fallback
    let fallbackSpace = "default";
    const statusRes = await this.fetchJSON<any>("/api/v1/system/status", undefined, 5000);
    if (statusRes.ok && typeof statusRes.result?.user === "string" && statusRes.result.user.trim()) {
      fallbackSpace = statusRes.result.user.trim();
    }

    // List scope root for actual namespaces
    const reserved = scope === "user" ? OVClient.RESERVED_USER : OVClient.RESERVED_AGENT;
    const entries = await this.ls(`viking://${scope}/`);
    const spaces = entries
      .filter(e => e.isDir && !e.name.startsWith(".") && !reserved.has(e.name))
      .map(e => e.name);

    if (spaces.length > 0) {
      // Prefer the fallback space if it exists, then "default", then first available
      let chosen = spaces[0];
      if (spaces.includes(fallbackSpace)) chosen = fallbackSpace;
      else if (spaces.includes("default")) chosen = "default";
      this.resolvedSpaces.set(scope, chosen);
      return chosen;
    }

    this.resolvedSpaces.set(scope, fallbackSpace);
    return fallbackSpace;
  }

  async resolveTargetUri(targetUri: string): Promise<string> {
    const trimmed = targetUri.trim().replace(/\/+$/, "");
    const m = trimmed.match(/^viking:\/\/(user|agent)(?:\/(.*))?$/);
    if (!m) return trimmed;
    const scope = m[1] as "user" | "agent";
    const rawRest = (m[2] ?? "").trim();
    if (!rawRest) return trimmed;
    const parts = rawRest.split("/").filter(Boolean);
    if (parts.length === 0) return trimmed;

    const reserved = scope === "user" ? OVClient.RESERVED_USER : OVClient.RESERVED_AGENT;
    if (!reserved.has(parts[0])) return trimmed; // already has space

    const space = await this.resolveScopeSpace(scope);
    return `viking://${scope}/${space}/${parts.join("/")}`;
  }
}

function uriBasename(uri: string): string {
  const cleaned = uri.replace(/\/+$/, "");
  const last = cleaned.lastIndexOf("/");
  return last >= 0 ? cleaned.slice(last + 1) : cleaned;
}

const ARCHIVE_ID_RE = /^archive_\d+$/;

/**
 * "The path is not there" as opposed to "the request failed". A session that
 * never committed has no `history` directory, which is an empty archive list,
 * not an unreachable server.
 */
function isNotFound(res: { status?: number; error?: any }): boolean {
  if (res.status === 404) return true;
  const code = String(res.error?.code ?? res.error?.message ?? "");
  return /NOT_?FOUND/i.test(code);
}

function stripTrailingSlash(uri: string): string {
  return uri.replace(/\/+$/, "");
}

/** `viking://…/sessions/x/history/archive_003` → `viking://…/sessions/x`. */
export function archiveUriToRoot(archiveUri: string): string {
  const cleaned = stripTrailingSlash(String(archiveUri ?? "").trim());
  return cleaned.replace(/\/history\/archive_\d+(?:\/.*)?$/, "");
}

/** First `archive_NNN` segment of a URI, "" when there is none. */
export function archiveIdFromUri(uri: string): string {
  for (const seg of stripTrailingSlash(String(uri ?? "")).split("/")) {
    if (ARCHIVE_ID_RE.test(seg)) return seg;
  }
  return "";
}

/** Path inside the archive directory, e.g. `messages.jsonl` / `.overview.md`. */
function archiveFileFromUri(uri: string): string {
  const segs = stripTrailingSlash(String(uri ?? "")).split("/");
  const at = segs.findIndex(s => ARCHIVE_ID_RE.test(s));
  if (at >= 0) return segs.slice(at + 1).join("/");
  return segs.length ? segs[segs.length - 1] : "";
}

function archiveIndex(archiveId: string): number {
  const n = Number.parseInt(archiveId.replace(/^archive_/, ""), 10);
  return Number.isFinite(n) ? n : -1;
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Drop a leading `---` … `---` YAML frontmatter block, if the text opens with
 * one, together with the blank line the server writes after it (its sidecars
 * are rendered as `---\n<yaml>\n---\n\n<body>\n`).
 */
function stripFrontmatter(text: string): string {
  const m = /^---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/.exec(text);
  if (!m) return text;
  return text.slice(m[0].length).replace(/^(?:[ \t]*\r?\n)+/, "");
}

function safeStringify(value: unknown): string {
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}

function renderValue(value: unknown): string {
  if (value == null) return "";
  return typeof value === "string" ? value : safeStringify(value);
}

/**
 * Render one `tool` part.
 *
 * The server serializes a tool part as `{type:"tool", tool_id, tool_name,
 * tool_uri, skill_uri, tool_status, tool_input?, tool_output?, …}` (see
 * `openviking/message/message.py::_part_to_dict`) — a call carries only
 * `tool_input`, a result carries `tool_output`. Older/foreign shapes
 * (`output` / `result` / `input` / `arguments`) are still accepted.
 *
 * The `[tool <name>]` label is emitted even when both sides are empty: a
 * message whose only part is a tool call must not read back as blank, which is
 * the whole promise of the `history` tool.
 */
function renderToolPart(part: any): string {
  const rawName = part.tool_name ?? part.toolName ?? part.name ?? "";
  const name = String(rawName ?? "").trim() || "unknown";
  const input = renderValue(part.tool_input ?? part.input ?? part.arguments ?? part.args);
  let output = renderValue(part.tool_output ?? part.output ?? part.result);
  if (!output && typeof part.content === "string") output = part.content;
  if (!output && typeof part.text === "string") output = part.text;
  // Large outputs are stored outside the message; point at them instead of
  // rendering an empty result.
  if (!output && typeof part.tool_output_ref === "string" && part.tool_output_ref) {
    output = `<output stored at ${part.tool_output_ref}>`;
  }

  const chunks: string[] = [];
  if (input) chunks.push(input);
  if (output) chunks.push(output);
  return chunks.length ? `[tool ${name}] ${chunks.join(" ")}` : `[tool ${name}]`;
}

/** Render one archived part; unknown shapes render to "" and are dropped. */
function renderPart(part: any): string {
  if (part == null) return "";
  if (typeof part === "string") return part;
  if (typeof part !== "object") return "";

  const type = typeof part.type === "string" ? part.type : "";
  const isTool = type.includes("tool")
    || part.tool_name != null || part.toolName != null
    || part.tool_output != null || part.tool_input != null;
  if (isTool) return renderToolPart(part);

  if (type === "context") {
    const uri = typeof part.uri === "string" ? part.uri : "";
    const abstract = typeof part.abstract === "string" ? part.abstract.trim() : "";
    if (!uri && !abstract) return "";
    return abstract ? `[context ${uri}] ${abstract}` : `[context ${uri}]`;
  }
  if (type === "image") return "[image]";

  if (typeof part.text === "string") return part.text;
  if (typeof part.content === "string") return part.content;
  return "";
}

function renderMessageText(parts: any[], content?: unknown): string {
  const chunks: string[] = [];
  for (const part of parts) {
    const rendered = renderPart(part);
    if (rendered) chunks.push(rendered);
  }
  if (chunks.length === 0 && typeof content === "string") return content;
  return chunks.join("\n");
}
