import { createHash } from "node:crypto";
import type { spawn } from "node:child_process";

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

export const localClientCache = new Map<string, LocalClientCacheEntry>();

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
  private readonly resolvedSpaceCache = new Map<string, string>();
  private userId: string | null = null;

  constructor(
    private readonly baseUrl: string,
    private readonly apiKey: string,
    private readonly timeoutMs: number,
  ) {}

  private async request<T>(path: string, init: RequestInit = {}, agentId?: string): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const headers = new Headers(init.headers ?? {});
      if (this.apiKey) {
        headers.set("X-API-Key", this.apiKey);
      }
      if (agentId) {
        headers.set("X-OpenViking-Agent", agentId);
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

  private async ls(uri: string, agentId: string): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      `/api/v1/fs/ls?uri=${encodeURIComponent(uri)}&output=original`,
      {},
      agentId
    );
  }

  private async getUserId(): Promise<string> {
    if (this.userId) {
      return this.userId;
    }
    try {
      const status = await this.request<{ user?: unknown }>("/api/v1/system/status");
      this.userId =
        typeof status.user === "string" && status.user.trim() ? status.user.trim() : "default";
    } catch {
      this.userId = "default";
    }
    return this.userId;
  }

  private async getRuntimeIdentity(agentId: string): Promise<RuntimeIdentity> {
    const userId = await this.getUserId();
    return { userId, agentId: agentId || "default" };
  }

  private async resolveScopeSpace(scope: ScopeName, agentId: string): Promise<string> {
    const cacheKey = `${scope}:${agentId}`;
    const cached = this.resolvedSpaceCache.get(cacheKey);
    if (cached) {
      return cached;
    }

    const identity = await this.getRuntimeIdentity(agentId);
    const fallbackSpace =
      scope === "user" ? identity.userId : md5Short(`${identity.userId}${identity.agentId}`);
    const reservedDirs = scope === "user" ? USER_STRUCTURE_DIRS : AGENT_STRUCTURE_DIRS;
    const preferredSpace =
      scope === "user" ? identity.userId : md5Short(`${identity.userId}${identity.agentId}`);

    try {
      const entries = await this.ls(`viking://${scope}`, agentId);
      const spaces = entries
        .filter((entry) => entry?.isDir === true)
        .map((entry) => (typeof entry.name === "string" ? entry.name.trim() : ""))
        .filter((name) => name && !name.startsWith(".") && !reservedDirs.has(name));

      if (spaces.length > 0) {
        if (spaces.includes(preferredSpace)) {
          this.resolvedSpaceCache.set(cacheKey, preferredSpace);
          return preferredSpace;
        }
        if (scope === "user" && spaces.includes("default")) {
          this.resolvedSpaceCache.set(cacheKey, "default");
          return "default";
        }
        if (spaces.length === 1) {
          this.resolvedSpaceCache.set(cacheKey, spaces[0]!);
          return spaces[0]!;
        }
      }
    } catch {
      // Fall back to identity-derived space when listing fails.
    }

    this.resolvedSpaceCache.set(cacheKey, fallbackSpace);
    return fallbackSpace;
  }

  private async normalizeTargetUri(targetUri: string, agentId: string): Promise<string> {
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
      agentId: string;
    },
  ): Promise<FindResult> {
    const normalizedTargetUri = await this.normalizeTargetUri(options.targetUri, options.agentId);
    const body = {
      query,
      target_uri: normalizedTargetUri,
      limit: options.limit,
      score_threshold: options.scoreThreshold,
    };
    return this.request<FindResult>("/api/v1/search/find", {
      method: "POST",
      body: JSON.stringify(body),
    }, options.agentId);
  }

  async read(uri: string, agentId: string): Promise<string> {
    return this.request<string>(
      `/api/v1/content/read?uri=${encodeURIComponent(uri)}`,
      {},
      agentId,
    );
  }

  async createSession(agentId: string): Promise<string> {
    const result = await this.request<{ session_id: string }>("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({}),
    }, agentId);
    return result.session_id;
  }

  async addSessionMessage(sessionId: string, role: string, content: string, agentId: string): Promise<void> {
    await this.request<{ session_id: string }>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
      {
        method: "POST",
        body: JSON.stringify({ role, content }),
      },
      agentId,
    );
  }

  /** GET session so server loads messages from storage before extract (workaround for AGFS visibility). */
  async getSession(sessionId: string, agentId: string): Promise<{ message_count?: number }> {
    return this.request<{ message_count?: number }>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}`,
      { method: "GET" },
      agentId,
    );
  }

  async extractSessionMemories(sessionId: string, agentId: string): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/extract`,
      { method: "POST", body: JSON.stringify({}) },
      agentId,
    );
  }

  async deleteSession(sessionId: string, agentId: string): Promise<void> {
    await this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }, agentId);
  }

  async deleteUri(uri: string, agentId: string): Promise<void> {
    await this.request(`/api/v1/fs?uri=${encodeURIComponent(uri)}&recursive=false`, {
      method: "DELETE",
    }, agentId);
  }
}
