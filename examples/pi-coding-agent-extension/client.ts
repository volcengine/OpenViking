import type { OVConfig } from "./config.js";
import type { OvHttpRequestOptions } from "./shared/ov-http.mjs";
import { createOvHttp } from "./shared/ov-http.mjs";

// --- OV API Response Shapes ---
// All OV responses wrap in: { status: "ok"|"error", result: T, error?: {...}, ... }
// This client normalizes to { ok, result } internally.
//
// Scope: session plumbing only — health, the OV session and its commit, plus the
// raw `fetchJSON` the shared recall/sync/profile modules are built on. Search,
// content reads, filesystem operations and resource ingest are the model's
// business and reach the server over MCP (`lib/mcp-bridge.mjs`), so their REST
// wrappers are gone rather than kept as a second, drifting path to the same
// endpoints.

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
      { defaultTimeoutMs: 10000, resolveActorPeerId: () => config.peerId },
    );
  }

  /** Core fetch wrapper. Returns { ok, result } after parsing OV's { status, result } envelope. */
  async fetchJSON<T>(path: string, init?: RequestInit, options?: OvHttpRequestOptions): Promise<OVResponse<T>> {
    return this.http(path, init, options);
  }

  // ========== Health ==========

  async health(): Promise<boolean> {
    const res = await this.fetchJSON<any>("/health", undefined, { timeoutMs: 5000 });
    this.connected = res.ok;
    return res.ok;
  }

  // ========== Sessions ==========

  /** GET /api/v1/sessions/{id} — session metadata */
  async getSession(sessionId: string, autoCreate = false): Promise<OVSessionMeta | null> {
    const q = autoCreate ? "?auto_create=true" : "";
    const res = await this.fetchJSON<OVSessionMeta>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}${q}`,
      undefined, { timeoutMs: 5000 },
    );
    return res.ok ? res.result : null;
  }

  /** GET /api/v1/sessions/{id}/context — assembled context with archive overview */
  async getSessionContext(sessionId: string, tokenBudget = 128000): Promise<OVSessionContext | null> {
    const res = await this.fetchJSON<OVSessionContext>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/context?token_budget=${tokenBudget}`,
      undefined, { timeoutMs: 10000 },
    );
    return res.ok ? res.result : null;
  }

  /** POST /api/v1/sessions/{id}/commit — commit session for archiving + extraction */
  async commitSessionResponse(
    sessionId: string,
    keepRecentCount = this.cfg.commitKeepRecentCount,
  ): Promise<OVCommitResponse> {
    const res = await this.fetchJSON<OVCommitResult>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`,
      { method: "POST", body: JSON.stringify({ keep_recent_count: keepRecentCount }) },
      { timeoutMs: 30000 },
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
}
