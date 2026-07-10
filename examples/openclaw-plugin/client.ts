import { randomUUID } from "node:crypto";
import { once } from "node:events";
import { createWriteStream } from "node:fs";
import { mkdtemp, readdir, readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, relative } from "node:path";

import { Zip, ZipDeflate } from "fflate";

import { defaultHttpTransport, type HttpTransport } from "./adapters/http-transport.js";
import {
  defaultResourcePackager,
  type ResourcePackager,
} from "./adapters/resource-packager.js";

export type FindResultItem = {
  uri: string;
  level?: number;
  abstract?: string;
  overview?: string;
  category?: string;
  score?: number;
  match_reason?: string;
};

export type FindResult = {
  memories?: FindResultItem[];
  resources?: FindResultItem[];
  skills?: FindResultItem[];
  total?: number;
};

export type FsListEntry = string | Record<string, unknown>;

export type FsListResult = FsListEntry[];

export type CaptureMode = "semantic" | "keyword";
function userSessionUri(sessionId: string): string {
  return `viking://user/sessions/${encodeURIComponent(sessionId)}`;
}

export type OpenVikingClientOptions = {
  transport?: HttpTransport;
  headers?: Record<string, string>;
  resourcePackager?: ResourcePackager;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
};

export type CommitSessionResult = {
  session_id: string;
  /** "accepted" (async), "skipped" (no archive), "completed", "failed", or "timeout" (wait mode). */
  status: string;
  task_id?: string;
  archive_uri?: string;
  archived?: boolean;
  /** Present when wait=true and extraction completed. Keyed by category. */
  memories_extracted?: Record<string, number>;
  error?: string;
  trace_id?: string;
};

export type OVMemoryPolicySwitch = {
  enabled?: boolean;
};

export type OVMemoryPolicy = {
  self?: OVMemoryPolicySwitch;
  peer?: OVMemoryPolicySwitch;
  memory_types?: string[];
};

export type TaskResult = {
  task_id: string;
  task_type: string;
  status: string;
  created_at: number;
  updated_at: number;
  resource_id?: string;
  result?: Record<string, unknown>;
  error?: string;
};

export type OVMessagePart = {
  type: string;
  text?: string;
  uri?: string;
  abstract?: string;
  context_type?: string;
  tool_id?: string;
  tool_name?: string;
  tool_input?: unknown;
  tool_output?: string;
  tool_status?: string;
  skill_uri?: string;
  duration_ms?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  tool_output_ref?: string;
  tool_output_truncated?: boolean;
  tool_output_original_chars?: number;
  tool_output_preview_chars?: number;
  tool_output_sha256?: string;
  tool_output_storage_uri?: string;
  tool_output_mime_type?: string;
  tool_output_source_ref?: string;
  tool_output_source_offset?: number;
  tool_output_source_limit?: number;
  tool_output_externalization_error?: string;
  tool_output_group_id?: string;
  tool_output_externalized_reason?: string;
  tool_output_group_original_chars?: number;
  tool_output_group_budget_chars?: number;
};

export type OVMessage = {
  id: string;
  role: string;
  parts: OVMessagePart[];
  created_at: string;
};

export type PreArchiveAbstract = {
  archive_id: string;
  abstract: string;
};

export type SessionContextResult = {
  latest_archive_overview: string;
  pre_archive_abstracts: PreArchiveAbstract[];
  messages: OVMessage[];
  estimatedTokens: number;
  stats: {
    totalArchives: number;
    includedArchives: number;
    droppedArchives: number;
    failedArchives: number;
    activeTokens: number;
    archiveTokens: number;
  };
};

export type ToolResultReadResult = {
  tool_result_id: string;
  content: string;
  offset: number;
  limit: number;
  offset_unit: "unicode_code_point";
  total_chars: number;
  has_more: boolean;
  metadata?: Record<string, unknown>;
};

export type ToolResultSearchResult = {
  tool_result_id: string;
  matches: Array<{
    offset: number;
    offset_unit: "unicode_code_point";
    snippet: string;
  }>;
};

export type ToolResultListResult = {
  tool_results: Array<Record<string, unknown>>;
};

export type SessionArchiveResult = {
  archive_id: string;
  abstract: string;
  overview: string;
  messages: OVMessage[];
};

export type AddResourceInput = {
  pathOrUrl: string;
  to?: string;
  parent?: string;
  reason?: string;
  instruction?: string;
  wait?: boolean;
  timeout?: number;
  strict?: boolean;
  ignoreDirs?: string;
  include?: string;
  exclude?: string;
  preserveStructure?: boolean;
};

export type AddResourceResult = {
  status?: string;
  root_uri?: string;
  temp_uri?: string;
  source_path?: string;
  warnings?: string[];
  errors?: string[];
  queue_status?: unknown;
  meta?: unknown;
};

export type AddSkillInput = {
  path?: string;
  data?: unknown;
  wait?: boolean;
  timeout?: number;
};

export type AddSkillResult = {
  status?: string;
  uri?: string;
  name?: string;
  auxiliary_files?: number;
  queue_status?: unknown;
};

const DEFAULT_WAIT_REQUEST_TIMEOUT_MS = 120_000;
export const DEFAULT_PHASE2_POLL_TIMEOUT_MS = 300_000;
const WAIT_REQUEST_TIMEOUT_BUFFER_MS = 5_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const MEMORY_URI_PATTERNS = [
  /^viking:\/\/user\/(?:[^/]+\/)?memories(?:\/|$)/,
];
const REMOTE_RESOURCE_PREFIXES = ["http://", "https://", "git@", "ssh://", "git://"];

export function isMemoryUri(uri: string): boolean {
  return MEMORY_URI_PATTERNS.some((pattern) => pattern.test(uri));
}

function isRemoteResourceSource(source: string): boolean {
  return REMOTE_RESOURCE_PREFIXES.some((prefix) => source.startsWith(prefix));
}

function toBlobPart(value: Buffer): ArrayBuffer {
  return value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength) as ArrayBuffer;
}

function resolveWaitRequestTimeoutMs(defaultTimeoutMs: number, waitTimeoutSeconds?: number): number {
  const requestedMs =
    typeof waitTimeoutSeconds === "number" && Number.isFinite(waitTimeoutSeconds) && waitTimeoutSeconds > 0
      ? Math.ceil(waitTimeoutSeconds * 1000) + WAIT_REQUEST_TIMEOUT_BUFFER_MS
      : DEFAULT_WAIT_REQUEST_TIMEOUT_MS;
  return Math.max(defaultTimeoutMs, requestedMs);
}

async function cleanupUploadTempPath(path?: string): Promise<void> {
  if (!path) {
    return;
  }
  await rm(path, { force: true }).catch(() => undefined);
  await rm(dirname(path), { recursive: true, force: true }).catch(() => undefined);
}

export class OpenVikingClient {
  private readonly transport: HttpTransport;
  private readonly configuredHeaders: Record<string, string>;
  private readonly now: () => number;
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly resourcePackager: ResourcePackager;

  constructor(
    private readonly baseUrl: string,
    private readonly apiKey: string,
    private readonly defaultAgentId: string,
    private readonly timeoutMs: number,
    /** When set, sent so ROOT keys or trusted deployments can select tenant identity. */
    private readonly accountId: string = "",
    private readonly userId: string = "",
    /** When set, logs routing for find + session writes (tenant headers + paths; never apiKey). */
    private readonly routingDebugLog?: (message: string) => void,
	    optionsOrLegacyUserScope: OpenVikingClientOptions | boolean = {},
	    _legacyAgentScope?: boolean,
	    legacyOptions?: OpenVikingClientOptions,
	  ) {
	    const options =
	      typeof optionsOrLegacyUserScope === "object" && optionsOrLegacyUserScope !== null
	        ? optionsOrLegacyUserScope
	        : (legacyOptions ?? {});
	    this.transport = options.transport ?? defaultHttpTransport;
    this.configuredHeaders = options.headers ?? {};
	    this.now = options.now ?? Date.now;
	    this.sleep = options.sleep ?? sleep;
    this.resourcePackager = options.resourcePackager ?? defaultResourcePackager;
  }

  getDefaultAgentId(): string {
    return this.defaultAgentId;
  }

  private resolveTenantHeaders():
    | { apiKey?: string; accountId?: string; userId?: string }
  {
    const apiKey = this.apiKey.trim();
    const accountId = this.accountId.trim();
    const userId = this.userId.trim();
    return {
      ...(apiKey ? { apiKey } : {}),
      ...(accountId ? { accountId } : {}),
      ...(userId ? { userId } : {}),
    };
  }

  private resolveActorPeerHeader(actorPeerId?: string): string | undefined {
    const value = actorPeerId?.trim();
    return value || undefined;
  }

  private resolveDefaultActorPeerHeader(): string {
    const peerPrefix = this.defaultAgentId.trim();
    return peerPrefix ? `${peerPrefix}_main` : "main";
  }

  private async emitRoutingDebug(
    label: string,
    detail: Record<string, unknown>,
    actorPeerId?: string,
  ): Promise<void> {
    if (!this.routingDebugLog) {
      return;
    }
    const tenantHeaders = this.resolveTenantHeaders();
    const actorPeerHeader = this.resolveActorPeerHeader(actorPeerId);
    this.routingDebugLog(
      `openviking: ${label} ` +
        JSON.stringify({
          ...detail,
          X_OpenViking_Account: tenantHeaders.accountId ?? null,
          X_OpenViking_User: tenantHeaders.userId ?? null,
          X_OpenViking_Actor_Peer: actorPeerHeader ?? null,
          session_vfs_hint: detail.sessionId
            ? userSessionUri(String(detail.sessionId))
            : undefined,
        }),
    );
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    requestTimeoutMs?: number,
    actorPeerId?: string,
  ): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), requestTimeoutMs ?? this.timeoutMs);
    try {
      const headers = new Headers(init.headers ?? {});
      const tenantHeaders = this.resolveTenantHeaders();
      if (tenantHeaders.apiKey) {
        headers.set("X-API-Key", tenantHeaders.apiKey);
      }
      if (tenantHeaders.accountId) {
        headers.set("X-OpenViking-Account", tenantHeaders.accountId);
      }
      if (tenantHeaders.userId) {
        headers.set("X-OpenViking-User", tenantHeaders.userId);
      }
      const actorPeerHeader = this.resolveActorPeerHeader(actorPeerId);
      if (actorPeerHeader) {
        headers.set("X-OpenViking-Actor-Peer", actorPeerHeader);
      }
      for (const [key, value] of Object.entries(this.configuredHeaders)) {
        if (key && typeof value === "string" && value.trim()) {
          headers.set(key, value);
        }
      }
      if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
      }

      const response = await this.transport(`${this.baseUrl}${path}`, {
        ...init,
        headers,
        signal: controller.signal,
      });

      const payload = (await response.json().catch(() => ({}))) as {
        status?: string;
        result?: T;
        error?: { code?: string; message?: string };
      };

      if (!response.ok || payload.status === "error") {
        const code = payload.error?.code ? ` [${payload.error.code}]` : "";
        const message = payload.error?.message ?? `HTTP ${response.status}`;
        throw new Error(`OpenViking request failed${code}: ${message}`);
      }

      return (payload.result ?? payload) as T;
    } finally {
      clearTimeout(timer);
    }
  }

  async healthCheck(requestTimeoutMs?: number, actorPeerId?: string): Promise<void> {
    await this.request<{ status: string }>(
      "/health",
      {},
      requestTimeoutMs,
      actorPeerId ?? this.resolveDefaultActorPeerHeader(),
    );
  }

  async createSession(
    sessionId: string,
    options?: { memoryPolicy?: OVMemoryPolicy },
  ): Promise<{ session_id: string; user?: unknown }> {
    const body: Record<string, unknown> = { session_id: sessionId };
    if (options?.memoryPolicy) {
      body.memory_policy = options.memoryPolicy;
    }
    return this.request<{ session_id: string; user?: unknown }>(
      "/api/v1/sessions",
      { method: "POST", body: JSON.stringify(body) },
    );
  }

  async ensureSession(
    sessionId: string,
    options?: { memoryPolicy?: OVMemoryPolicy },
  ): Promise<boolean> {
    try {
      await this.createSession(sessionId, options);
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (message.includes("[ALREADY_EXISTS]")) {
        return false;
      }
      throw err;
    }
  }

  async find(
    query: string,
    options: {
      targetUri?: string;
      limit?: number;
      scoreThreshold?: number;
      contextType?: string | string[];
      actorPeerId?: string;
    },
    legacyActorPeerId?: string,
  ): Promise<FindResult> {
    const targetUri = options.targetUri?.trim().replace(/\/+$/, "") ?? "";
    const body: {
      query: string;
      target_uri?: string;
      limit?: number;
      score_threshold?: number;
      context_type?: string | string[];
    } = {
      query,
      limit: options.limit,
      score_threshold: options.scoreThreshold,
      context_type: options.contextType,
    };
    if (targetUri) {
      body.target_uri = targetUri;
    }
    const actorPeerId = this.resolveActorPeerHeader(options.actorPeerId ?? legacyActorPeerId);
    const tenantHeaders = this.resolveTenantHeaders();
    this.routingDebugLog?.(
      `openviking: find POST ${this.baseUrl}/api/v1/search/find ` +
        JSON.stringify({
          X_OpenViking_Account: tenantHeaders.accountId ?? null,
          X_OpenViking_User: tenantHeaders.userId ?? null,
          X_OpenViking_Actor_Peer: actorPeerId ?? null,
          target_uri: targetUri || null,
          target_uri_input: options.targetUri,
          query:
            query.length > 4000
              ? `${query.slice(0, 4000)}…(+${query.length - 4000} more chars)`
              : query,
          limit: body.limit,
          score_threshold: body.score_threshold ?? null,
          context_type: body.context_type ?? null,
        }),
    );
    return this.request<FindResult>("/api/v1/search/find", {
      method: "POST",
      body: JSON.stringify(body),
    }, undefined, actorPeerId);
  }

  async read(uri: string, actorPeerId?: string): Promise<string> {
    return this.request<string>(
      `/api/v1/content/read?uri=${encodeURIComponent(uri)}`,
      {},
      undefined,
      actorPeerId,
    );
  }

  async list(
    uri: string,
    options?: {
      recursive?: boolean;
      simple?: boolean;
      output?: "agent" | "original";
      absLimit?: number;
      showAllHidden?: boolean;
      nodeLimit?: number;
      actorPeerId?: string;
    },
  ): Promise<FsListResult> {
    const normalizedUri = uri.trim().replace(/\/+$/, "");
    const params = new URLSearchParams({
      uri: normalizedUri,
      recursive: String(options?.recursive ?? false),
      simple: String(options?.simple ?? false),
      output: options?.output ?? "agent",
      abs_limit: String(options?.absLimit ?? 256),
      show_all_hidden: String(options?.showAllHidden ?? false),
      node_limit: String(options?.nodeLimit ?? 1000),
    });
    return this.request<FsListResult>(
      `/api/v1/fs/ls?${params.toString()}`,
      {},
      undefined,
      options?.actorPeerId,
    );
  }

  async readToolResult(
    sessionId: string,
    toolResultId: string,
    options?: { offset?: number; limit?: number; includeMetadata?: boolean },
  ): Promise<ToolResultReadResult> {
    const params = new URLSearchParams();
    if (options?.offset !== undefined) params.set("offset", String(options.offset));
    if (options?.limit !== undefined) params.set("limit", String(options.limit));
    if (options?.includeMetadata !== undefined) {
      params.set("include_metadata", String(options.includeMetadata));
    }
    const query = params.toString();
    return this.request<ToolResultReadResult>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/tool-results/${encodeURIComponent(toolResultId)}${query ? `?${query}` : ""}`,
      {},
    );
  }

  async searchToolResult(
    sessionId: string,
    toolResultId: string,
    queryText: string,
    options?: { limit?: number; contextChars?: number },
  ): Promise<ToolResultSearchResult> {
    const params = new URLSearchParams({ q: queryText });
    if (options?.limit !== undefined) params.set("limit", String(options.limit));
    if (options?.contextChars !== undefined) {
      params.set("context_chars", String(options.contextChars));
    }
    return this.request<ToolResultSearchResult>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/tool-results/${encodeURIComponent(toolResultId)}/search?${params.toString()}`,
      {},
    );
  }

  async listToolResults(
    sessionId: string,
    options?: { toolName?: string; limit?: number },
  ): Promise<ToolResultListResult> {
    const params = new URLSearchParams();
    if (options?.toolName) params.set("tool_name", options.toolName);
    if (options?.limit !== undefined) params.set("limit", String(options.limit));
    const query = params.toString();
    return this.request<ToolResultListResult>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/tool-results${query ? `?${query}` : ""}`,
      {},
    );
  }

  async uploadTempFile(filePath: string, actorPeerId?: string): Promise<string> {
    const form = await this.resourcePackager.createTempUploadBody(filePath);
    const result = await this.request<{ temp_file_id: string }>(
      "/api/v1/resources/temp_upload",
      { method: "POST", body: form },
      undefined,
      actorPeerId,
    );
    if (!result.temp_file_id) {
      throw new Error("OpenViking temp upload did not return temp_file_id");
    }
    return result.temp_file_id;
  }

  async zipDirectoryForUpload(dirPath: string): Promise<string> {
    const rootStats = await stat(dirPath);
    if (!rootStats.isDirectory()) {
      throw new Error(`Not a directory: ${dirPath}`);
    }

    const zipDir = await mkdtemp(join(tmpdir(), "openviking-openclaw-upload-"));
    const zipPath = join(zipDir, `${basename(dirPath).replace(/[^a-zA-Z0-9._-]/g, "_")}-${randomUUID()}.zip`);
    const output = createWriteStream(zipPath);
    const outputClosed = once(output, "close");
    const outputErrored = once(output, "error").then(([err]) => Promise.reject(err));
    const zip = new Zip((err, chunk, final) => {
      if (err) {
        output.destroy(err);
        return;
      }
      if (chunk?.length) {
        output.write(Buffer.from(chunk));
      }
      if (final) {
        output.end();
      }
    });

    const walk = async (currentDir: string) => {
      const entries = await readdir(currentDir, { withFileTypes: true });
      for (const entry of entries) {
        const fullPath = join(currentDir, entry.name);
        if (entry.isDirectory()) {
          await walk(fullPath);
          continue;
        }
        if (!entry.isFile()) {
          continue;
        }
        const relPath = relative(dirPath, fullPath).replace(/\\/g, "/");
        if (!relPath || relPath.startsWith("../") || relPath.includes("/../")) {
          throw new Error(`Unsafe relative path while zipping: ${relPath}`);
        }
        const file = new ZipDeflate(relPath);
        zip.add(file);
        file.push(new Uint8Array(await readFile(fullPath)), true);
      }
    };
    try {
      await walk(dirPath);
      zip.end();
      await Promise.race([outputClosed, outputErrored]);
    } catch (err) {
      zip.terminate();
      output.destroy(err as Error);
      await cleanupUploadTempPath(zipPath);
      throw err;
    }
    return zipPath;
  }

  async addResource(input: AddResourceInput, actorPeerId?: string): Promise<AddResourceResult> {
    const pathOrUrl = input.pathOrUrl.trim();
    if (!pathOrUrl) {
      throw new Error("pathOrUrl is required");
    }
    if (input.to && input.parent) {
      throw new Error("Cannot specify both 'to' and 'parent'.");
    }

    const body: Record<string, unknown> = {
      to: input.to,
      parent: input.parent,
      reason: input.reason ?? "",
      instruction: input.instruction ?? "",
      wait: input.wait ?? false,
      timeout: input.timeout,
      strict: input.strict ?? false,
      ignore_dirs: input.ignoreDirs,
      include: input.include,
      exclude: input.exclude,
    };
    if (typeof input.preserveStructure === "boolean") {
      body.preserve_structure = input.preserveStructure;
    }

    let packagedSource: Awaited<ReturnType<ResourcePackager["prepareResourceSource"]>> | undefined;
    const requestTimeoutMs =
      input.wait ? resolveWaitRequestTimeoutMs(this.timeoutMs, input.timeout) : undefined;
    try {
      packagedSource = await this.resourcePackager.prepareResourceSource(pathOrUrl);
      if (packagedSource.kind === "remote") {
        body.path = packagedSource.path;
      } else {
        if (packagedSource.sourceName) {
          body.source_name = packagedSource.sourceName;
        }
        body.temp_file_id = await this.uploadTempFile(packagedSource.uploadPath, actorPeerId);
      }
      return this.request<AddResourceResult>(
        "/api/v1/resources",
        { method: "POST", body: JSON.stringify(body) },
        requestTimeoutMs,
        actorPeerId,
      );
    } finally {
      await this.resourcePackager.cleanup(packagedSource);
    }
  }

  async addSkill(input: AddSkillInput, actorPeerId?: string): Promise<AddSkillResult> {
    const hasPath = typeof input.path === "string" && input.path.trim().length > 0;
    const hasData = input.data !== undefined && input.data !== null;
    if (hasPath === hasData) {
      throw new Error("Provide exactly one of 'path' or 'data' for skill import.");
    }

    const body: Record<string, unknown> = {
      wait: input.wait ?? false,
      timeout: input.timeout,
    };
    let packagedSource: Awaited<ReturnType<ResourcePackager["prepareLocalUploadSource"]>> | undefined;
    const requestTimeoutMs =
      input.wait ? resolveWaitRequestTimeoutMs(this.timeoutMs, input.timeout) : undefined;
    try {
      if (hasPath) {
        const skillPath = input.path!.trim();
        packagedSource = await this.resourcePackager.prepareLocalUploadSource(skillPath);
        if (packagedSource.kind !== "upload") {
          throw new Error(`Path is not a file or directory: ${skillPath}`);
        }
        body.temp_file_id = await this.uploadTempFile(packagedSource.uploadPath, actorPeerId);
      } else {
        body.data = input.data;
      }
      return this.request<AddSkillResult>(
        "/api/v1/skills",
        { method: "POST", body: JSON.stringify(body) },
        requestTimeoutMs,
        actorPeerId,
      );
    } finally {
      await this.resourcePackager.cleanup(packagedSource);
    }
  }

  async addSessionMessage(
    sessionId: string,
    role: string,
    parts: Array<{
      type: "text" | "tool" | "context";
      text?: string;
      tool_name?: string;
      tool_output?: string;
      tool_status?: string;
      tool_input?: Record<string, unknown>;
      tool_id?: string;
      tool_output_ref?: string;
      tool_output_truncated?: boolean;
      tool_output_original_chars?: number;
      tool_output_preview_chars?: number;
      tool_output_sha256?: string;
      tool_output_storage_uri?: string;
      tool_output_mime_type?: string;
      tool_output_source_ref?: string;
      tool_output_source_offset?: number;
      tool_output_source_limit?: number;
      tool_output_group_id?: string;
      tool_output_externalized_reason?: string;
      tool_output_group_original_chars?: number;
      tool_output_group_budget_chars?: number;
      uri?: string;
      abstract?: string;
      context_type?: "memory" | "resource" | "skill";
    }>,
    actorPeerId?: string,
    createdAt?: string,
    peerId?: string,
  ): Promise<void> {
    const body: {
      role: string;
      peer_id?: string;
      parts: typeof parts;
      created_at?: string;
    } = { role, parts };
    if (createdAt) {
      body.created_at = createdAt;
    }
    if (peerId) {
      body.peer_id = peerId;
    }
    await this.emitRoutingDebug(
      "session message POST (with parts)",
      {
        path: `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
        sessionId,
        role,
        peer_id: peerId ?? null,
        partCount: parts.length,
        created_at: createdAt ?? null,
      },
      actorPeerId,
    );
    await this.request<{ session_id: string }>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      undefined,
      actorPeerId,
    );
  }

  /** GET session — server auto-creates if absent; returns session meta including message stats and token usage. */
  async getSession(sessionId: string, actorPeerId?: string): Promise<{
    message_count?: number;
    commit_count?: number;
    last_commit_at?: string;
    pending_tokens?: number;
    llm_token_usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  }> {
    return this.request<{
      message_count?: number;
      commit_count?: number;
      last_commit_at?: string;
      pending_tokens?: number;
      llm_token_usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
    }>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}`,
      { method: "GET" },
      undefined,
      actorPeerId,
    );
  }

  /**
   * Commit a session: archive (Phase 1) and extract memories (Phase 2).
   *
   * wait=false (default): returns immediately after Phase 1 with task_id.
   * wait=true: after Phase 1, polls GET /tasks/{task_id} until Phase 2
   *   completes (or times out), then returns the merged result.
   */
  async commitSession(
    sessionId: string,
    options?: {
      wait?: boolean;
      timeoutMs?: number;
      /**
       * WM v2: number of most-recent messages to keep live after commit.
       * Forwarded as `keep_recent_count` in the POST body. 0 (default)
       * preserves the pre-v2 "archive everything" behavior.
      */
      keepRecentCount?: number;
      agentId?: string;
    },
  ): Promise<CommitSessionResult> {
    const keepRecentCount =
      options?.keepRecentCount != null && Number.isFinite(options.keepRecentCount)
        ? Math.max(0, Math.floor(options.keepRecentCount))
        : 0;
    await this.emitRoutingDebug(
      "session commit POST (archive + memory extraction)",
      {
        path: `/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`,
        sessionId,
        wait: options?.wait ?? false,
        keepRecentCount,
      },
      options?.agentId,
    );
    const body: Record<string, unknown> = {};
    if (keepRecentCount > 0) {
      body.keep_recent_count = keepRecentCount;
    }
    const result = await this.request<CommitSessionResult>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`,
      { method: "POST", body: JSON.stringify(body) },
      undefined,
      options?.agentId,
    );

    if (!options?.wait || !result.task_id) {
      return result;
    }

    // Client-side poll until Phase 2 finishes
    const deadline = this.now() + (options.timeoutMs ?? DEFAULT_PHASE2_POLL_TIMEOUT_MS);
    const pollInterval = 500;
    while (this.now() < deadline) {
      await this.sleep(pollInterval);
      const task = await this.getTask(result.task_id, options.agentId).catch(() => null);
      if (!task) break;
      if (task.status === "completed") {
        const taskResult = (task.result ?? {}) as Record<string, unknown>;
        const memoriesExtracted = (taskResult.memories_extracted ?? {}) as Record<string, number>;
        result.status = "completed";
        result.memories_extracted = memoriesExtracted;
        return result;
      }
      if (task.status === "failed") {
        result.status = "failed";
        result.error = task.error;
        return result;
      }
    }
    result.status = "timeout";
    return result;
  }

  /** Poll a background task by ID. */
  async getTask(taskId: string, actorPeerId?: string): Promise<TaskResult> {
    return this.request<TaskResult>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}`,
      { method: "GET" },
      undefined,
      actorPeerId,
    );
  }

  async getSessionContext(
    sessionId: string,
    tokenBudget: number = 128_000,
    actorPeerId?: string,
  ): Promise<SessionContextResult> {
    return this.request(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/context?token_budget=${tokenBudget}`,
      { method: "GET" },
      undefined,
      actorPeerId,
    );
  }

  async getSessionArchive(
    sessionId: string,
    archiveId: string,
    actorPeerId?: string,
  ): Promise<SessionArchiveResult> {
    return this.request(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/archives/${encodeURIComponent(archiveId)}`,
      { method: "GET" },
      undefined,
      actorPeerId,
    );
  }

  async grepSessionArchives(
    sessionId: string,
    pattern: string,
    options: {
      archiveId?: string;
      caseInsensitive?: boolean;
      nodeLimit?: number;
      levelLimit?: number;
    } = {},
  ): Promise<{
    matches: Array<{ line: number; uri: string; content: string }>;
    count: number;
    match_count?: number;
    files_scanned?: number;
  }> {
    const baseUri = `${userSessionUri(sessionId)}/history`;
    const uri = options.archiveId ? `${baseUri}/${options.archiveId}` : baseUri;
    return this.request(
      "/api/v1/search/grep",
      {
        method: "POST",
        body: JSON.stringify({
          uri,
          pattern,
          case_insensitive: options.caseInsensitive ?? true,
          ...(options.nodeLimit !== undefined ? { node_limit: options.nodeLimit } : {}),
          ...(options.levelLimit !== undefined ? { level_limit: options.levelLimit } : {}),
        }),
      },
    );
  }

  async deleteSession(sessionId: string): Promise<void> {
    await this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
  }
  async deleteUri(uri: string, actorPeerId?: string): Promise<void> {
    await this.request(`/api/v1/fs?uri=${encodeURIComponent(uri)}&recursive=false`, {
      method: "DELETE",
    }, undefined, actorPeerId);
  }
}
