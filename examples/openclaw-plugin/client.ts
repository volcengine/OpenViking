import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { basename, extname } from "node:path";
import type { spawn } from "node:child_process";

export type AttachmentItem = {
  uri: string;
  mime_type: string;
  abstract: string;
};

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

function md5Short(input: string): string {
  return createHash("md5").update(input).digest("hex").slice(0, 12);
}

export function isMemoryUri(uri: string): boolean {
  return MEMORY_URI_PATTERNS.some((pattern) => pattern.test(uri));
}

export class OpenVikingClient {
  private resolvedSpaceByScope: Partial<Record<ScopeName, string>> = {};
  private runtimeIdentity: RuntimeIdentity | null = null;

  constructor(
    private readonly baseUrl: string,
    private readonly apiKey: string,
    private agentId: string,
    private readonly timeoutMs: number,
  ) {}

  /**
   * Dynamically switch the agent identity for multi-agent memory isolation.
   * When a shared client serves multiple agents (e.g. in OpenClaw multi-agent
   * gateway), call this before each agent's recall/capture to route memories
   * to the correct agent_space = md5(user_id + agent_id)[:12].
   * Clears cached space resolution so the next request re-derives agent_space.
   */
  setAgentId(newAgentId: string): void {
    if (newAgentId && newAgentId !== this.agentId) {
      this.agentId = newAgentId;
      // Clear cached identity and spaces — they depend on agentId
      this.runtimeIdentity = null;
      this.resolvedSpaceByScope = {};
    }
  }

  getAgentId(): string {
    return this.agentId;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const headers = new Headers(init.headers ?? {});
      if (this.apiKey) {
        headers.set("X-API-Key", this.apiKey);
      }
      if (this.agentId) {
        headers.set("X-OpenViking-Agent", this.agentId);
      }
      if (init.body && !headers.has("Content-Type")) {
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

  private async ls(uri: string): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      `/api/v1/fs/ls?uri=${encodeURIComponent(uri)}&output=original`,
    );
  }

  private async getRuntimeIdentity(): Promise<RuntimeIdentity> {
    if (this.runtimeIdentity) {
      return this.runtimeIdentity;
    }
    const fallback: RuntimeIdentity = { userId: "default", agentId: this.agentId || "default" };
    try {
      const status = await this.request<{ user?: unknown }>("/api/v1/system/status");
      const userId =
        typeof status.user === "string" && status.user.trim() ? status.user.trim() : "default";
      this.runtimeIdentity = { userId, agentId: this.agentId || "default" };
      return this.runtimeIdentity;
    } catch {
      this.runtimeIdentity = fallback;
      return fallback;
    }
  }

  private async resolveScopeSpace(scope: ScopeName): Promise<string> {
    const cached = this.resolvedSpaceByScope[scope];
    if (cached) {
      return cached;
    }

    const identity = await this.getRuntimeIdentity();
    const fallbackSpace =
      scope === "user" ? identity.userId : md5Short(`${identity.userId}:${identity.agentId}`);
    const reservedDirs = scope === "user" ? USER_STRUCTURE_DIRS : AGENT_STRUCTURE_DIRS;
    const preferredSpace =
      scope === "user" ? identity.userId : md5Short(`${identity.userId}:${identity.agentId}`);

    try {
      const entries = await this.ls(`viking://${scope}`);
      const spaces = entries
        .filter((entry) => entry?.isDir === true)
        .map((entry) => (typeof entry.name === "string" ? entry.name.trim() : ""))
        .filter((name) => name && !name.startsWith(".") && !reservedDirs.has(name));

      if (spaces.length > 0) {
        if (spaces.includes(preferredSpace)) {
          this.resolvedSpaceByScope[scope] = preferredSpace;
          return preferredSpace;
        }
        if (scope === "user" && spaces.includes("default")) {
          this.resolvedSpaceByScope[scope] = "default";
          return "default";
        }
        if (spaces.length === 1) {
          this.resolvedSpaceByScope[scope] = spaces[0]!;
          return spaces[0]!;
        }
      }
    } catch {
      // Fall back to identity-derived space when listing fails.
    }

    this.resolvedSpaceByScope[scope] = fallbackSpace;
    return fallbackSpace;
  }

  private async normalizeTargetUri(targetUri: string): Promise<string> {
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

    const space = await this.resolveScopeSpace(scope);
    return `viking://${scope}/${space}/${parts.join("/")}`;
  }

  async find(
    query: string,
    options: {
      targetUri: string;
      limit: number;
      scoreThreshold?: number;
    },
  ): Promise<FindResult> {
    const normalizedTargetUri = await this.normalizeTargetUri(options.targetUri);
    const body = {
      query,
      target_uri: normalizedTargetUri,
      limit: options.limit,
      score_threshold: options.scoreThreshold,
    };
    return this.request<FindResult>("/api/v1/search/find", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  async read(uri: string): Promise<string> {
    return this.request<string>(
      `/api/v1/content/read?uri=${encodeURIComponent(uri)}`,
    );
  }

  async createSession(): Promise<string> {
    const result = await this.request<{ session_id: string }>("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({}),
    });
    return result.session_id;
  }

  async addSessionMessage(sessionId: string, role: string, content: string): Promise<void> {
    await this.request<{ session_id: string }>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
      {
        method: "POST",
        body: JSON.stringify({ role, content }),
      },
    );
  }

  /** GET session so server loads messages from storage before extract (workaround for AGFS visibility). */
  async getSession(sessionId: string): Promise<{ message_count?: number }> {
    return this.request<{ message_count?: number }>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}`,
      { method: "GET" },
    );
  }

  async extractSessionMemories(sessionId: string): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/extract`,
      { method: "POST", body: JSON.stringify({}) },
    );
  }

  async deleteSession(sessionId: string): Promise<void> {
    await this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
  }

  async deleteUri(uri: string): Promise<void> {
    await this.request(`/api/v1/fs?uri=${encodeURIComponent(uri)}&recursive=false`, {
      method: "DELETE",
    });
  }

  /**
   * Upload local files to viking://resources/attachments/ and return structured metadata.
   * Uses content-addressed storage (SHA-256 hash in URI) for deduplication.
   * Concurrency is limited to 3 to avoid VLM avalanche on large batches.
   * Each upload gets an independent 60s timeout; individual failures return null (not thrown).
   */
  async storeAttachments(filePaths: string[]): Promise<AttachmentItem[]> {
    const CONCURRENCY_LIMIT = 3;
    const results: AttachmentItem[] = [];

    for (let i = 0; i < filePaths.length; i += CONCURRENCY_LIMIT) {
      const chunk = filePaths.slice(i, i + CONCURRENCY_LIMIT);

      const settled = await Promise.allSettled(
        chunk.map(async (filePath): Promise<AttachmentItem | null> => {
          const perFileController = new AbortController();
          const perFileTimer = setTimeout(() => perFileController.abort(), 60_000);

          try {
            // Validate file exists and is not empty
            const fileStat = await stat(filePath);
            if (!fileStat.isFile() || fileStat.size === 0) {
              console.warn(`[memory-openviking] storeAttachments skipping non-file or empty: ${filePath}`);
              return null;
            }

            // Compute SHA-256 hash for content-addressed dedup
            const fileHash = await hashFile(filePath);
            const safeFileName = basename(filePath).replace(/[^a-zA-Z0-9._-]/g, "_");
            const destUri = `viking://resources/attachments/${fileHash}_${safeFileName}`;

            // Step 1: temp_upload (multipart form)
            const formData = new FormData();
            const fileBuffer = await new Promise<Buffer>((resolve, reject) => {
              const chunks: Buffer[] = [];
              const stream = createReadStream(filePath);
              stream.on("data", (chunk: Buffer) => chunks.push(chunk));
              stream.on("end", () => resolve(Buffer.concat(chunks)));
              stream.on("error", reject);
            });
            const blob = new Blob([fileBuffer]);
            formData.append("file", blob, safeFileName);

            const uploadResp = await fetch(`${this.baseUrl}/api/v1/resources/temp_upload`, {
              method: "POST",
              headers: this.apiKey ? { "X-API-Key": this.apiKey } : {},
              body: formData,
              signal: perFileController.signal,
            });

            if (!uploadResp.ok) {
              const errText = await uploadResp.text().catch(() => "");
              console.warn(`[memory-openviking] temp_upload failed for ${filePath}: HTTP ${uploadResp.status} ${errText}`);
              return null;
            }

            const uploadResult = (await uploadResp.json()) as { result?: { path?: string } };
            const tempPath = uploadResult?.result?.path;
            if (!tempPath) {
              console.warn(`[memory-openviking] temp_upload returned no path for ${filePath}`);
              return null;
            }

            // Step 2: addResource (triggers VLM description + multimodal embedding)
            const addResp = await fetch(`${this.baseUrl}/api/v1/resources`, {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                ...(this.apiKey ? { "X-API-Key": this.apiKey } : {}),
              },
              body: JSON.stringify({
                path: tempPath,
                to: destUri,
                wait: true,
              }),
              signal: perFileController.signal,
            });

            if (!addResp.ok) {
              const errText = await addResp.text().catch(() => "");
              console.warn(`[memory-openviking] addResource failed for ${filePath}: HTTP ${addResp.status} ${errText}`);
              return null;
            }

            const addResult = (await addResp.json()) as {
              result?: { root_uri?: string; abstract?: string };
            };

            return {
              uri: addResult?.result?.root_uri ?? destUri,
              mime_type: getMimeType(filePath),
              abstract: addResult?.result?.abstract ?? "",
            };
          } catch (err) {
            console.warn(`[memory-openviking] storeAttachments failed for ${filePath}:`, err);
            return null;
          } finally {
            clearTimeout(perFileTimer);
          }
        }),
      );

      for (const s of settled) {
        if (s.status === "fulfilled" && s.value !== null) {
          results.push(s.value);
        }
      }
    }

    return results;
  }
}

/** Stream SHA-256 hash of a file (no full-file buffer in memory). */
async function hashFile(filePath: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const hash = createHash("sha256");
    const stream = createReadStream(filePath);
    stream.on("data", (chunk: Buffer) => hash.update(chunk));
    stream.on("end", () => resolve(hash.digest("hex").slice(0, 16)));
    stream.on("error", reject);
  });
}

const MIME_MAP: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".svg": "image/svg+xml",
  ".mp4": "video/mp4",
  ".webm": "video/webm",
  ".mp3": "audio/mpeg",
  ".wav": "audio/wav",
  ".pdf": "application/pdf",
  ".json": "application/json",
  ".txt": "text/plain",
  ".md": "text/markdown",
  ".csv": "text/csv",
};

function getMimeType(filePath: string): string {
  return MIME_MAP[extname(filePath).toLowerCase()] ?? "application/octet-stream";
}
