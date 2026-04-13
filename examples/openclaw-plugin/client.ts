import { createHash, randomUUID } from "node:crypto";
import type { spawn } from "node:child_process";
import { once } from "node:events";
import { createWriteStream } from "node:fs";
import { mkdtemp, readdir, readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, relative } from "node:path";

import { Zip, ZipDeflate } from "fflate";

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

export type CaptureMode = "semantic" | "keyword";
export type ScopeName = "user" | "agent";
export type RuntimeIdentity = {
  userId: string;
  agentId: string;
};
export type LocalClientCacheEntry = {
  client: OpenVikingClient;
  process: ReturnType<typeof spawn> | null;
};

export type PendingClientEntry = {
  promise: Promise<OpenVikingClient>;
  resolve: (c: OpenVikingClient) => void;
  reject: (err: unknown) => void;
};

export type CommitSessionResult = {
  session_id: string;
  /** "accepted" (async), "completed", "failed", or "timeout" (wait mode). */
  status: string;
  task_id?: string;
  archive_uri?: string;
  archived?: boolean;
  /** Present when wait=true and extraction completed. Keyed by category. */
  memories_extracted?: Record<string, number>;
  error?: string;
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

export const localClientCache = new Map<string, LocalClientCacheEntry>();

// Module-level pending promise map: shared across all plugin registrations so
// that both [gateway] and [plugins] contexts await the same promise and
// don't create duplicate pending promises that never resolve.
export const localClientPendingPromises = new Map<string, PendingClientEntry>();

const MEMORY_URI_PATTERNS = [
  /^viking:\/\/user\/(?:[^/]+\/)?memories(?:\/|$)/,
  /^viking:\/\/agent\/(?:[^/]+\/)?memories(?:\/|$)/,
];
const USER_STRUCTURE_DIRS = new Set(["memories"]);
const AGENT_STRUCTURE_DIRS = new Set(["memories", "skills", "instructions", "workspaces"]);
const REMOTE_RESOURCE_PREFIXES = ["http://", "https://", "git@", "ssh://", "git://"];

function md5Short(input: string): string {
  return createHash("md5").update(input).digest("hex").slice(0, 12);
}

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
  private spaceCache = new Map<string, Partial<Record<ScopeName, string>>>();
  private identityCache = new Map<string, RuntimeIdentity>();

  constructor(
    private readonly baseUrl: string,
    private readonly apiKey: string,
    private readonly defaultAgentId: string,
    private readonly timeoutMs: number,
    /** When set (or defaulted), sent so ROOT key can access tenant-scoped APIs. */
    private readonly accountId: string = "",
    private readonly userId: string = "",
    /** When set, logs routing for find + session writes (tenant headers + paths; never apiKey). */
    private readonly routingDebugLog?: (message: string) => void,
  ) {}

  getDefaultAgentId(): string {
    return this.defaultAgentId;
  }

  private async emitRoutingDebug(
    label: string,
    detail: Record<string, unknown>,
    agentId?: string,
  ): Promise<void> {
    if (!this.routingDebugLog) {
      return;
    }
    const effectiveAgentId = agentId ?? this.defaultAgentId;
    const identity = await this.getRuntimeIdentity(agentId);
    this.routingDebugLog(
      `openviking: ${label} ` +
        JSON.stringify({
          ...detail,
          X_OpenViking_Agent: effectiveAgentId,
          X_OpenViking_Account: this.accountId.trim() || "default",
          X_OpenViking_User: this.userId.trim() || "default",
          resolved_user_id: identity.userId,
          session_vfs_hint: detail.sessionId
            ? `viking://session/${identity.userId}/${String(detail.sessionId)}`
            : undefined,
        }),
    );
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    agentId?: string,
    requestTimeoutMs?: number,
  ): Promise<T> {
    const effectiveAgentId = agentId ?? this.defaultAgentId;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), requestTimeoutMs ?? this.timeoutMs);
    try {
      const headers = new Headers(init.headers ?? {});
      if (this.apiKey) {
        headers.set("X-API-Key", this.apiKey);
      }
      headers.set("X-OpenViking-Account", this.accountId.trim() || "default");
      headers.set("X-OpenViking-User", this.userId.trim() || "default");
      if (effectiveAgentId) {
        headers.set("X-OpenViking-Agent", effectiveAgentId);
      }
      if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
      }

      const response = await fetch(`${this.baseUrl}${path}`, {
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

  async healthCheck(): Promise<void> {
    await this.request<{ status: string }>("/health");
  }

  private async ls(uri: string, agentId?: string): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      `/api/v1/fs/ls?uri=${encodeURIComponent(uri)}&output=original`,
      {},
      agentId,
    );
  }

  private async getRuntimeIdentity(agentId?: string): Promise<RuntimeIdentity> {
    const effectiveAgentId = agentId ?? this.defaultAgentId;
    const cached = this.identityCache.get(effectiveAgentId);
    if (cached) {
      return cached;
    }
    const fallback: RuntimeIdentity = { userId: "default", agentId: effectiveAgentId || "default" };
    try {
      const status = await this.request<{ user?: unknown }>("/api/v1/system/status", {}, agentId);
      const userId =
        typeof status.user === "string" && status.user.trim() ? status.user.trim() : "default";
      const identity: RuntimeIdentity = { userId, agentId: effectiveAgentId || "default" };
      this.identityCache.set(effectiveAgentId, identity);
      return identity;
    } catch {
      this.identityCache.set(effectiveAgentId, fallback);
      return fallback;
    }
  }

  private async resolveScopeSpace(scope: ScopeName, agentId?: string): Promise<string> {
    const effectiveAgentId = agentId ?? this.defaultAgentId;
    const agentScopes = this.spaceCache.get(effectiveAgentId);
    const cached = agentScopes?.[scope];
    if (cached) {
      return cached;
    }

    const identity = await this.getRuntimeIdentity(agentId);
    const fallbackSpace =
      scope === "user" ? identity.userId : md5Short(`${identity.userId}:${identity.agentId}`);
    const reservedDirs = scope === "user" ? USER_STRUCTURE_DIRS : AGENT_STRUCTURE_DIRS;
    const preferredSpace =
      scope === "user" ? identity.userId : md5Short(`${identity.userId}:${identity.agentId}`);

    const saveSpace = (space: string) => {
      const existing = this.spaceCache.get(effectiveAgentId) ?? {};
      existing[scope] = space;
      this.spaceCache.set(effectiveAgentId, existing);
    };

    try {
      const entries = await this.ls(`viking://${scope}`, agentId);
      const spaces = entries
        .filter((entry) => entry?.isDir === true)
        .map((entry) => (typeof entry.name === "string" ? entry.name.trim() : ""))
        .filter((name) => name && !name.startsWith(".") && !reservedDirs.has(name));

      if (spaces.length > 0) {
        if (spaces.includes(preferredSpace)) {
          saveSpace(preferredSpace);
          return preferredSpace;
        }
        if (scope === "user" && spaces.includes("default")) {
          saveSpace("default");
          return "default";
        }
        if (spaces.length === 1) {
          saveSpace(spaces[0]!);
          return spaces[0]!;
        }
      }
    } catch {
      // Fall back to identity-derived space when listing fails.
    }

    saveSpace(fallbackSpace);
    return fallbackSpace;
  }

  private async normalizeTargetUri(targetUri: string, agentId?: string): Promise<string> {
    const trimmed = targetUri.trim().replace(/\/+$/, "");
    const match = trimmed.match(/^viking:\/\/(user|agent)(?:\/(.*))?$/);
    if (!match) {
      return trimmed;
    }
    const scope = match[1] as ScopeName;
    const rawRest = (match[2] ?? "").trim();
    if (!rawRest) {
      return trimmed;
    }
    const parts = rawRest.split("/").filter(Boolean);
    if (parts.length === 0) {
      return trimmed;
    }

    const reservedDirs = scope === "user" ? USER_STRUCTURE_DIRS : AGENT_STRUCTURE_DIRS;
    if (!reservedDirs.has(parts[0]!)) {
      return trimmed;
    }

    const space = await this.resolveScopeSpace(scope, agentId);
    return `viking://${scope}/${space}/${parts.join("/")}`;
  }

  async find(
    query: string,
    options: {
      targetUri: string;
      limit: number;
      scoreThreshold?: number;
    },
    agentId?: string,
  ): Promise<FindResult> {
    const normalizedTargetUri = await this.normalizeTargetUri(options.targetUri, agentId);
    const body = {
      query,
      target_uri: normalizedTargetUri,
      limit: options.limit,
      score_threshold: options.scoreThreshold,
    };
    const effectiveAgentId = agentId ?? this.defaultAgentId;
    const identity = await this.getRuntimeIdentity(agentId);
    this.routingDebugLog?.(
      `openviking: find POST ${this.baseUrl}/api/v1/search/find ` +
        JSON.stringify({
          X_OpenViking_Agent: effectiveAgentId,
          X_OpenViking_Account: this.accountId.trim() || "default",
          X_OpenViking_User: this.userId.trim() || "default",
          resolved_user_id: identity.userId,
          target_uri: normalizedTargetUri,
          target_uri_input: options.targetUri,
          query:
            query.length > 4000
              ? `${query.slice(0, 4000)}…(+${query.length - 4000} more chars)`
              : query,
          limit: body.limit,
          score_threshold: body.score_threshold ?? null,
        }),
    );
    return this.request<FindResult>("/api/v1/search/find", {
      method: "POST",
      body: JSON.stringify(body),
    }, agentId);
  }

  async read(uri: string, agentId?: string): Promise<string> {
    return this.request<string>(
      `/api/v1/content/read?uri=${encodeURIComponent(uri)}`,
      {},
      agentId,
    );
  }

  async uploadTempFile(filePath: string, agentId?: string): Promise<string> {
    const fileBytes = await readFile(filePath);
    const form = new FormData();
    form.append(
      "file",
      new Blob([toBlobPart(fileBytes)], { type: "application/octet-stream" }),
      basename(filePath),
    );
    const result = await this.request<{ temp_file_id: string }>(
      "/api/v1/resources/temp_upload",
      { method: "POST", body: form },
      agentId,
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

  async addResource(input: AddResourceInput, agentId?: string): Promise<AddResourceResult> {
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

    let cleanupPath: string | undefined;
    const requestTimeoutMs =
      input.wait ? resolveWaitRequestTimeoutMs(this.timeoutMs, input.timeout) : undefined;
    try {
      if (isRemoteResourceSource(pathOrUrl)) {
        body.path = pathOrUrl;
      } else {
        const localStats = await stat(pathOrUrl);
        let uploadPath = pathOrUrl;
        if (localStats.isDirectory()) {
          uploadPath = await this.zipDirectoryForUpload(pathOrUrl);
          cleanupPath = uploadPath;
          body.source_name = basename(pathOrUrl);
        } else if (!localStats.isFile()) {
          throw new Error(`Path is not a file or directory: ${pathOrUrl}`);
        }
        body.temp_file_id = await this.uploadTempFile(uploadPath, agentId);
      }
      return this.request<AddResourceResult>(
        "/api/v1/resources",
        { method: "POST", body: JSON.stringify(body) },
        agentId,
        requestTimeoutMs,
      );
    } finally {
      await cleanupUploadTempPath(cleanupPath);
    }
  }

  async addSkill(input: AddSkillInput, agentId?: string): Promise<AddSkillResult> {
    const hasPath = typeof input.path === "string" && input.path.trim().length > 0;
    const hasData = input.data !== undefined && input.data !== null;
    if (hasPath === hasData) {
      throw new Error("Provide exactly one of 'path' or 'data' for skill import.");
    }

    const body: Record<string, unknown> = {
      wait: input.wait ?? false,
      timeout: input.timeout,
    };
    let cleanupPath: string | undefined;
    const requestTimeoutMs =
      input.wait ? resolveWaitRequestTimeoutMs(this.timeoutMs, input.timeout) : undefined;
    try {
      if (hasPath) {
        const skillPath = input.path!.trim();
        const localStats = await stat(skillPath);
        let uploadPath = skillPath;
        if (localStats.isDirectory()) {
          uploadPath = await this.zipDirectoryForUpload(skillPath);
          cleanupPath = uploadPath;
        } else if (!localStats.isFile()) {
          throw new Error(`Path is not a file or directory: ${skillPath}`);
        }
        body.temp_file_id = await this.uploadTempFile(uploadPath, agentId);
      } else {
        body.data = input.data;
      }
      return this.request<AddSkillResult>(
        "/api/v1/skills",
        { method: "POST", body: JSON.stringify(body) },
        agentId,
        requestTimeoutMs,
      );
    } finally {
      await cleanupUploadTempPath(cleanupPath);
    }
  }

  async addSessionMessage(
    sessionId: string,
    role: string,
    content: string,
    agentId?: string,
    createdAt?: string,
  ): Promise<void> {
    const body: {
      role: string;
      content: string;
      created_at?: string;
    } = { role, content };
    if (createdAt) {
      body.created_at = createdAt;
    }
    await this.emitRoutingDebug(
      "session message POST",
      {
        path: `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
        sessionId,
        role,
        contentChars: content.length,
        created_at: createdAt ?? null,
      },
      agentId,
    );
    await this.request<{ session_id: string }>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      agentId,
    );
  }

  /** GET session — server auto-creates if absent; returns session meta including message stats and token usage. */
  async getSession(sessionId: string, agentId?: string): Promise<{
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
      agentId,
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
    options?: { wait?: boolean; timeoutMs?: number; agentId?: string },
  ): Promise<CommitSessionResult> {
    await this.emitRoutingDebug(
      "session commit POST (archive + memory extraction)",
      {
        path: `/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`,
        sessionId,
        wait: options?.wait ?? false,
      },
      options?.agentId,
    );
    const result = await this.request<CommitSessionResult>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`,
      { method: "POST", body: JSON.stringify({}) },
      options?.agentId,
    );

    if (!options?.wait || !result.task_id) {
      return result;
    }

    // Client-side poll until Phase 2 finishes
    const deadline = Date.now() + (options.timeoutMs ?? DEFAULT_PHASE2_POLL_TIMEOUT_MS);
    const pollInterval = 500;
    while (Date.now() < deadline) {
      await sleep(pollInterval);
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
  async getTask(taskId: string, agentId?: string): Promise<TaskResult> {
    return this.request<TaskResult>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}`,
      { method: "GET" },
      agentId,
    );
  }

  async getSessionContext(
    sessionId: string,
    tokenBudget: number = 128_000,
    agentId?: string,
  ): Promise<SessionContextResult> {
    return this.request(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/context?token_budget=${tokenBudget}`,
      { method: "GET" },
      agentId,
    );
  }

  async getSessionArchive(
    sessionId: string,
    archiveId: string,
    agentId?: string,
  ): Promise<SessionArchiveResult> {
    return this.request(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/archives/${encodeURIComponent(archiveId)}`,
      { method: "GET" },
      agentId,
    );
  }

  async deleteSession(sessionId: string, agentId?: string): Promise<void> {
    await this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }, agentId);
  }
  async deleteUri(uri: string, agentId?: string): Promise<void> {
    await this.request(`/api/v1/fs?uri=${encodeURIComponent(uri)}&recursive=false`, {
      method: "DELETE",
    }, agentId);
  }
}
