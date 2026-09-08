import type { OVConfig } from "./config.js";
import { createOvHttp } from "./shared/ov-http.mjs";

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
  task_id?: string;
  archive_uri?: string;
  trace_id?: string;
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
  private http: ReturnType<typeof createOvHttp>;
  connected: boolean = false;

  /** Read-only access to config (for value access across modules). */
  readonly cfg: OVConfig;

  constructor(config: OVConfig) {
    this.cfg = config;
    this.http = createOvHttp(
      { ...config, baseUrl: config.endpoint.replace(/\/+$/, "") },
      { resolveActorPeerId: () => config.peerId },
    );
  }

  /** Core fetch wrapper. Returns { ok, result } after parsing OV's { status, result } envelope. */
  async fetchJSON<T>(path: string, init?: RequestInit, timeoutMs = 10000): Promise<OVResponse<T>> {
    return this.http(path, init, { timeoutMs });
  }

  // ========== Health ==========

  async health(): Promise<boolean> {
    const res = await this.fetchJSON<any>("/health", undefined, 5000);
    this.connected = res.ok;
    return res.ok;
  }

  // ========== Sessions ==========

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
}

function uriBasename(uri: string): string {
  const cleaned = uri.replace(/\/+$/, "");
  const last = cleaned.lastIndexOf("/");
  return last >= 0 ? cleaned.slice(last + 1) : cleaned;
}
