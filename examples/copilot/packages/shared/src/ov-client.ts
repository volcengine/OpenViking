/**
 * Typed HTTP client for the OpenViking REST API.
 *
 * One-to-one with the endpoints the Claude Code memory plugin's
 * scripts/lib/ov-session.mjs already exercises in production. The
 * Copilot plugins reuse this client unchanged so any future fix to the
 * wire protocol lands in one place.
 *
 * Endpoints:
 *   GET    /health                                          — health
 *   POST   /api/v1/search/find                              — recall
 *   GET    /api/v1/content/read?uri=...                     — read
 *   POST   /api/v1/sessions/{id}/messages                   — appendTurns
 *   POST   /api/v1/sessions/{id}/commit                     — commit
 *   GET    /api/v1/sessions/{id}/context?token_budget=N     — fetchArchiveOverview
 *
 * Headers (only sent when the corresponding cfg field is non-empty):
 *   Authorization: Bearer <apiKey>
 *   X-OpenViking-Account: <accountId>
 *   X-OpenViking-User:    <userId>
 *   X-OpenViking-Agent:   <agentId>
 *
 * Bypass: when cfg.bypassSession is true, or when bypassSessionPatterns
 * matches the supplied sessionId / cwd, every method short-circuits with
 * a synthetic success result and writes a telemetry line to the logger.
 * Hosts wire bypass into a single config; nothing else needs to know.
 */

import type { PluginConfig } from "./config.js";
import type { DebugLogger } from "./debug/logger.js";

/** Single hit from `/search/find`. Server returns more fields; these are the ones callers depend on. */
export interface RecallHit {
  uri: string;
  /** Semantic type the server attaches: `memory` | `skill` | `resource` | etc. */
  type?: string;
  /** Cosine score in [0, 1]. */
  score?: number;
  abstract?: string;
  content?: string;
  [extra: string]: unknown;
}

/** Single conversation turn pushed into a session's message log. */
export interface OVTurn {
  role: "user" | "assistant";
  /** Plain-text body. Mutually exclusive with `parts` when the host uses parts-mode. */
  content?: string;
  /** Structured parts (tier-1 capture). Forwarded as-is. */
  parts?: unknown[];
}

export type OVResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: { message: string; status?: number } };

export interface RecallOptions {
  limit: number;
  sessionId: string;
  /** Optional `target_uri` scope, e.g. `viking://agent/memories`. */
  targetUri?: string;
  /** Server-side score floor; default 0 to let the caller's ranker decide. */
  scoreThreshold?: number;
  /** Per-call timeout override. Falls back to `cfg.timeoutMs`. */
  timeoutMs?: number;
}

export interface ReadOptions {
  offset?: number;
  limit?: number;
  timeoutMs?: number;
}

export interface CommitOptions {
  force?: boolean;
}

export interface OVClientBypassContext {
  /** Host-side session id (e.g. CC session_id or workspace + chat id). */
  hostSessionId?: string;
  /** Working directory of the host process. */
  cwd?: string;
}

export interface OVClientOptions {
  logger?: DebugLogger;
  bypassContext?: OVClientBypassContext;
  /** Inject a fetch implementation for tests. Default: `globalThis.fetch`. */
  fetchImpl?: typeof fetch;
}

/**
 * Convert a single `*` glob (no /) or `**` glob (any) into a RegExp.
 * Mirrors the CC plugin's globToRe.
 */
function globToRe(glob: string): RegExp {
  let re = "^";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i] ?? "";
    if (c === "*") {
      if (glob[i + 1] === "*") {
        re += ".*";
        i++;
      } else {
        re += "[^/]*";
      }
    } else if (/[.+?^${}()|[\]\\]/.test(c)) {
      re += "\\" + c;
    } else {
      re += c;
    }
  }
  re += "$";
  return new RegExp(re);
}

export class OVClient {
  private readonly cfg: PluginConfig;
  private readonly logger?: DebugLogger;
  private readonly bypassContext: OVClientBypassContext;
  private readonly fetchImpl: typeof fetch;
  private readonly bypassRes: RegExp[];

  constructor(cfg: PluginConfig, opts: OVClientOptions = {}) {
    this.cfg = cfg;
    this.logger = opts.logger?.child("ov-client");
    this.bypassContext = opts.bypassContext ?? {};
    this.fetchImpl = opts.fetchImpl ?? ((globalThis as { fetch?: typeof fetch }).fetch as typeof fetch);
    this.bypassRes = (cfg.bypassSessionPatterns ?? []).map(globToRe);

    if (!this.fetchImpl) {
      throw new Error(
        "OVClient: no fetch implementation available. Pass `fetchImpl` in options or run on Node 18+/a runtime with global fetch.",
      );
    }
  }

  /** True when bypass is active for the configured context. */
  isBypassed(): boolean {
    if (this.cfg.bypassSession) return true;
    if (this.bypassRes.length === 0) return false;
    const haystacks: string[] = [];
    if (this.bypassContext.hostSessionId) haystacks.push(this.bypassContext.hostSessionId);
    if (this.bypassContext.cwd) haystacks.push(this.bypassContext.cwd);
    if (haystacks.length === 0) return false;
    return this.bypassRes.some((re) => haystacks.some((h) => re.test(h)));
  }

  // ----- public methods ---------------------------------------------------

  async health(): Promise<OVResult<unknown>> {
    if (this.isBypassed()) return this.bypassed("health", { bypassed: true });
    const res = await this.fetchJSON("GET", "/health");
    return res;
  }

  async recall(query: string, opts: RecallOptions): Promise<OVResult<RecallHit[]>> {
    if (this.isBypassed()) return this.bypassed("recall", []);

    const body: Record<string, unknown> = {
      query,
      limit: opts.limit,
      score_threshold: opts.scoreThreshold ?? 0,
    };
    if (opts.targetUri) body["target_uri"] = opts.targetUri;
    if (opts.sessionId) body["session_id"] = opts.sessionId;

    const res = await this.fetchJSON<unknown>("POST", "/api/v1/search/find", {
      jsonBody: body,
      timeoutMs: opts.timeoutMs,
    });
    if (!res.ok) return res;
    return { ok: true, value: flattenRecallBuckets(res.value) };
  }

  async read(uri: string, opts: ReadOptions = {}): Promise<OVResult<string>> {
    if (this.isBypassed()) return this.bypassed("read", "");
    const qs = new URLSearchParams({ uri });
    if (opts.offset !== undefined) qs.set("offset", String(Math.max(0, Math.floor(opts.offset))));
    if (opts.limit !== undefined) qs.set("limit", String(Math.max(0, Math.floor(opts.limit))));

    const res = await this.fetchJSON<unknown>("GET", `/api/v1/content/read?${qs.toString()}`, {
      timeoutMs: opts.timeoutMs,
    });
    if (!res.ok) return res;
    return { ok: true, value: stringifyReadValue(res.value) };
  }

  async appendTurns(sessionId: string, turns: OVTurn[]): Promise<OVResult<unknown>> {
    if (this.isBypassed()) return this.bypassed("appendTurns", { skipped: turns.length });
    if (turns.length === 0) return { ok: true, value: { written: 0 } };

    const path = `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`;
    let written = 0;
    for (const turn of turns) {
      const res = await this.fetchJSON<unknown>("POST", path, { jsonBody: turn });
      if (!res.ok) {
        this.logger?.log("append_turn_failed", { sessionId, written, error: res.error.message });
        return res;
      }
      written++;
    }
    return { ok: true, value: { written } };
  }

  async commit(sessionId: string, opts: CommitOptions = {}): Promise<OVResult<unknown>> {
    if (this.isBypassed()) return this.bypassed("commit", { skipped: true });
    const path = `/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`;
    const body = opts.force ? { force: true } : {};
    return this.fetchJSON("POST", path, { jsonBody: body });
  }

  async forget(uri: string, opts: { recursive?: boolean } = {}): Promise<OVResult<unknown>> {
    if (this.isBypassed()) return this.bypassed("forget", { skipped: true });
    const qs = new URLSearchParams({ uri });
    if (opts.recursive) qs.set("recursive", "true");
    const path = `/api/v1/fs?${qs.toString()}`;
    return this.fetchJSON("DELETE", path);
  }

  async fetchArchiveOverview(
    sessionId: string,
    budgetTokens: number,
  ): Promise<OVResult<string | null>> {
    if (this.isBypassed()) return this.bypassed("fetchArchiveOverview", null);
    const path =
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/context?token_budget=${Math.max(0, Math.floor(budgetTokens))}`;
    const res = await this.fetchJSON<{ latest_archive_overview?: string } | null>("GET", path);
    if (!res.ok) {
      // 404 (session does not exist) is the most common shape; surface as
      // a successful "no overview yet" rather than an error so callers can
      // treat the resume-prime path as best-effort.
      if (res.error.status === 404) return { ok: true, value: null };
      return res;
    }
    const overview = res.value && typeof res.value === "object"
      ? res.value.latest_archive_overview ?? null
      : null;
    return { ok: true, value: overview };
  }

  // ----- internals --------------------------------------------------------

  private bypassed<T>(method: string, value: T): OVResult<T> {
    this.logger?.log("bypassed", { method });
    return { ok: true, value };
  }

  private buildHeaders(): Record<string, string> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (this.cfg.apiKey) headers["Authorization"] = `Bearer ${this.cfg.apiKey}`;
    if (this.cfg.accountId) headers["X-OpenViking-Account"] = this.cfg.accountId;
    if (this.cfg.userId) headers["X-OpenViking-User"] = this.cfg.userId;
    if (this.cfg.agentId) headers["X-OpenViking-Agent"] = this.cfg.agentId;
    return headers;
  }

  private async fetchJSON<T = unknown>(
    method: "GET" | "POST" | "DELETE",
    path: string,
    opts: { jsonBody?: unknown; timeoutMs?: number } = {},
  ): Promise<OVResult<T>> {
    const url = `${this.cfg.baseUrl}${path}`;
    const timeoutMs = Math.max(1000, opts.timeoutMs ?? this.cfg.timeoutMs);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const init: RequestInit = {
        method,
        headers: this.buildHeaders(),
        signal: controller.signal,
      };
      if (opts.jsonBody !== undefined) {
        init.body = JSON.stringify(opts.jsonBody);
      }
      const res = await this.fetchImpl(url, init);
      const body = await res.json().catch(() => ({}));
      const wrappedStatus = (body as { status?: string })?.status;
      if (!res.ok || wrappedStatus === "error") {
        const err = (body as { error?: { message?: string } })?.error;
        return {
          ok: false,
          error: { message: err?.message ?? `HTTP ${res.status}`, status: res.status },
        };
      }
      const value = (body as { result?: T })?.result ?? (body as T);
      return { ok: true, value };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.logger?.log("fetch_failed", { url, message });
      return { ok: false, error: { message } };
    } finally {
      clearTimeout(timer);
    }
  }
}

/**
 * `/search/find` returns `{result: {memories: [...], skills: [...], ...}}`
 * — a bucketed shape. Flatten across all buckets and stamp the bucket
 * name onto each item as `type` (when the server didn't set one) so
 * downstream renderers can label items without the caller stitching the
 * source label back. Singular-form bucket names are normalised
 * (`memories` → `memory`) to match the CC plugin's `_sourceType`.
 */
function flattenRecallBuckets(raw: unknown): RecallHit[] {
  if (Array.isArray(raw)) return raw as RecallHit[];
  if (!raw || typeof raw !== "object") return [];
  const out: RecallHit[] = [];
  for (const [bucket, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!Array.isArray(value)) continue;
    const typeLabel = singulariseBucketName(bucket);
    for (const item of value) {
      if (item && typeof item === "object") {
        const hit = item as RecallHit;
        if (typeof hit.type !== "string" || !hit.type) hit.type = typeLabel;
        out.push(hit);
      }
    }
  }
  return out;
}

function singulariseBucketName(name: string): string {
  if (name.endsWith("ies")) return `${name.slice(0, -3)}y`;
  if (name.endsWith("s") && !name.endsWith("ss")) return name.slice(0, -1);
  return name;
}

function stringifyReadValue(value: unknown): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}
