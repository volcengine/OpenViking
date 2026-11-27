import type { OVConfig } from "./config.js";
import type { OvHttpRequestOptions } from "./shared/ov-http.mjs";
import { createOvHttp } from "./shared/ov-http.mjs";

// --- OV API Response Shapes ---
// All OV responses wrap in: { status: "ok"|"error", result: T, error?: {...}, ... }
// This client normalizes to { ok, result } internally.
//
// Scope: what only this extension sends — takeover's commit and archive reads,
// plus the raw `fetchJSON` sealing is built on. Capture, commits, recall and the
// session opening are the shared flow's. Search,
// content reads, filesystem operations and resource ingest are the model's
// business and reach the server over MCP (`lib/mcp-bridge.mjs`), so their REST
// wrappers are gone rather than kept as a second, drifting path to the same
// endpoints.

export interface OVCommitResult {
  /**
   * `accepted` = phase 1 wrote `history/archive_NNN/messages.jsonl` and a
   * background task is generating the Working Memory; `skipped` = nothing to
   * archive (see `reason`, e.g. `no_messages`). Older builds omit the field.
   */
  status?: "accepted" | "skipped" | string;
  /** Whether phase 1 created an archive. */
  archived?: boolean;
  effective_enable_working_memory?: boolean;
  reason?: string;
  task_id?: string;
  /** `null` on a `skipped` commit — the server sends the key either way. */
  archive_uri?: string | null;
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

  /** POST /api/v1/sessions/{id}/commit — commit session for archiving + extraction */
  async commitSessionResponse(
    sessionId: string,
    keepRecentCount = 0,
    timeoutMs = 30000,
    enableWorkingMemory?: boolean,
  ): Promise<OVCommitResponse> {
    const res = await this.fetchJSON<OVCommitResult>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`,
      { method: "POST", body: JSON.stringify({ keep_recent_count: keepRecentCount, enable_working_memory: enableWorkingMemory }) },
      { timeoutMs },
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

  /**
   * Working Memory of one archive: `GET /content/read?uri=<archive_uri>/.overview.md`.
   *
   * This reads the Working Memory that belongs to a *specific* commit, keyed by
   * the `archive_uri` that commit returned — unlike the session `/context`, whose
   * `latest_archive_overview` reflects whatever the session's newest archive is
   * and can point at an older `/context` summary that this takeover did not
   * produce. Returns null until commit phase 2 writes the sidecar (the server
   * answers 404 until then). Other read failures throw so takeover can log a
   * read failure separately from an unfinished archive. The sidecar is stored as
   * OKF Markdown — `---\n<yaml>\n---\n\n<body>\n` — and `content/read` only
   * strips that framing for memory URIs, not session archives, so the
   * frontmatter is removed here. A file present but empty counts as not ready
   * (null), so a poller cannot be fooled by an empty write. Takeover reads it
   * inside pi event handlers, so one read is capped well below their budget.
   */
  async readArchiveOverview(archiveUri: string, timeoutMs = 5000): Promise<string | null> {
    const base = String(archiveUri ?? "").trim().replace(/\/+$/, "");
    if (!base) return null;
    const res = await this.fetchJSON<string>(
      `/api/v1/content/read?uri=${encodeURIComponent(`${base}/.overview.md`)}`,
      undefined, { timeoutMs },
    );
    if (!res.ok) {
      if (res.status === 404) return null;
      const detail = res.error?.message || res.error?.code || `HTTP ${res.status ?? 0}`;
      throw new Error(`archive overview read failed: ${detail}`);
    }
    if (typeof res.result !== "string") return null;
    const body = stripFrontmatter(res.result);
    return body.trim() ? body : null;
  }

  /**
   * Terminal state of one archive, from the markers the server itself uses
   * (`Session._archive_terminal_state`): `.done` once commit phase 2 completed
   * — it is written last, after the Working Memory when that is enabled, and
   * records `enable_working_memory: false` when it is not — and `.failed.json`
   * once phase 2 failed for good. "pending" while neither exists; null when the
   * server could not be asked. Unlike task records, the markers do not expire.
   */
  async getArchiveState(archiveUri: string, timeoutMs = 10000): Promise<"completed" | "failed" | "pending" | null> {
    const base = String(archiveUri ?? "").trim().replace(/\/+$/, "");
    if (!base) return null;
    const deadline = Date.now() + timeoutMs;
    for (const [marker, state] of [[".done", "completed"], [".failed.json", "failed"]] as const) {
      const remaining = deadline - Date.now();
      if (remaining <= 0) return null;
      const res = await this.fetchJSON<string>(
        `/api/v1/content/read?uri=${encodeURIComponent(`${base}/${marker}`)}`,
        undefined, { timeoutMs: Math.min(5000, remaining) },
      );
      if (res.ok) return state;
      if (res.status !== 404) return null;
    }
    return "pending";
  }
}

/**
 * Drop a leading `---` … `---` YAML frontmatter block, together with the blank
 * line the server writes after it (its sidecars render as `---\n<yaml>\n---\n\n
 * <body>\n`). Reused rule from the experimental fork's client.
 */
function stripFrontmatter(text: string): string {
  const m = /^---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/.exec(text);
  if (!m) return text;
  return text.slice(m[0].length).replace(/^(?:[ \t]*\r?\n)+/, "");
}
