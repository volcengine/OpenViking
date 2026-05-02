/**
 * OpenViking Memory MCP Server for Claude Code
 *
 * Exposes OpenViking long-term memory as MCP tools:
 *   - memory_recall  : semantic search across memories
 *   - memory_store   : extract and persist new memories
 *   - memory_forget  : delete memories by URI or query
 *
 * Ported from the OpenClaw context-engine plugin (openclaw-plugin/).
 * Adapted for Claude Code's MCP server interface (stdio transport).
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { createHash } from "node:crypto";

// ---------------------------------------------------------------------------
// Types (ported from openclaw-plugin/client.ts)
// ---------------------------------------------------------------------------

type FindResultItem = {
  uri: string;
  level?: number;
  abstract?: string;
  overview?: string;
  category?: string;
  score?: number;
  match_reason?: string;
};

type FindResult = {
  memories?: FindResultItem[];
  resources?: FindResultItem[];
  skills?: FindResultItem[];
  total?: number;
};

type ScopeName = "user" | "agent";

// ---------------------------------------------------------------------------
// Configuration — loaded from the Claude Code client config.
// Env var: OPENVIKING_CC_CONFIG_FILE
// Default: ~/.openviking/claude-code-memory-plugin/config.json
//
// In local mode, apiKey defaults to the local OpenViking server config:
// OPENVIKING_CONFIG_FILE or ~/.openviking/ov.conf
// ---------------------------------------------------------------------------

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";

const DEFAULT_CLIENT_CONFIG_PATH = join(
  homedir(),
  ".openviking",
  "claude-code-memory-plugin",
  "config.json",
);
const DEFAULT_SERVER_CONFIG_PATH = join(homedir(), ".openviking", "ov.conf");

function fatal(message: string): never {
  process.stderr.write(`[openviking-memory] ${message}\n`);
  process.exit(1);
}

function resolveEnvVars(value: string): string {
  return value.replace(/\$\{([^}]+)\}/g, (_, envVar) => {
    const envValue = process.env[envVar];
    if (typeof envValue !== "string" || envValue === "") {
      fatal(`Environment variable ${envVar} is not set`);
    }
    return envValue;
  });
}

function resolveString(value: unknown, fallback: string): string {
  if (typeof value === "string" && value.trim()) return resolveEnvVars(value.trim());
  return fallback;
}

function resolveConfigPath(rawValue: unknown, fallback: string): string {
  return resolvePath(resolveString(rawValue, fallback).replace(/^~/, homedir()));
}

function normalizeBaseUrl(value: unknown): string {
  return resolveString(value, "").replace(/\/+$/, "");
}

function requireBaseUrl(value: unknown): string {
  const resolved = normalizeBaseUrl(value);
  if (!resolved) {
    fatal("Claude Code client config: baseUrl is required when mode is \"remote\"");
  }
  return resolved;
}

function clampPort(value: unknown): number {
  return Math.max(1, Math.min(65535, Math.floor(num(value, 1933))));
}

function loadRequiredJson(configPath: string, label: string): Record<string, unknown> {
  let raw: string;
  try {
    raw = readFileSync(configPath, "utf-8");
  } catch (err: unknown) {
    const code = (err as { code?: string })?.code;
    const msg = code === "ENOENT"
      ? `${label} not found: ${configPath}\n  Create it and set at least: { "mode": "local" }`
      : `Failed to read ${label}: ${configPath} — ${(err as Error)?.message || String(err)}`;
    fatal(msg);
  }

  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      fatal(`${label} must contain a JSON object: ${configPath}`);
    }
    return parsed as Record<string, unknown>;
  } catch (err: unknown) {
    fatal(`Invalid JSON in ${configPath}: ${(err as Error)?.message || String(err)}`);
  }
}

function loadOptionalJson(configPath: string): { file: Record<string, unknown> | null; error: string | null } {
  try {
    const parsed = JSON.parse(readFileSync(configPath, "utf-8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { file: null, error: `JSON root must be an object: ${configPath}` };
    }
    return { file: parsed as Record<string, unknown>, error: null };
  } catch (err: unknown) {
    if ((err as { code?: string })?.code === "ENOENT") {
      return { file: null, error: null };
    }
    return { file: null, error: (err as Error)?.message || String(err) };
  }
}

function num(val: unknown, fallback: number): number {
  if (typeof val === "number" && Number.isFinite(val)) return val;
  if (typeof val === "string" && val.trim()) {
    const n = Number(val);
    if (Number.isFinite(n)) return n;
  }
  return fallback;
}

const clientConfigPath = resolveConfigPath(
  process.env.OPENVIKING_CC_CONFIG_FILE,
  DEFAULT_CLIENT_CONFIG_PATH,
);
const clientFile = loadRequiredJson(clientConfigPath, "Claude Code client config");
const mode = clientFile.mode === "remote" ? "remote" : "local";
const serverConfigPath = resolveConfigPath(
  process.env.OPENVIKING_CONFIG_FILE,
  DEFAULT_SERVER_CONFIG_PATH,
);
const serverConfigResult = mode === "local"
  ? loadOptionalJson(serverConfigPath)
  : { file: null, error: null };
const serverCfg = (serverConfigResult.file?.server ?? {}) as Record<string, unknown>;

const config = {
  mode,
  configPath: clientConfigPath,
  serverConfigPath,
  serverConfigError: serverConfigResult.error,
  baseUrl: mode === "remote"
    ? requireBaseUrl(clientFile.baseUrl)
    : `http://127.0.0.1:${clampPort(serverCfg.port)}`,
  apiKey: resolveString(clientFile.apiKey, "") || (mode === "local" ? resolveString(serverCfg.root_api_key, "") : ""),
  agentId: resolveString(clientFile.agentId, "claude-code"),
  timeoutMs: Math.max(1000, Math.floor(num(clientFile.timeoutMs, 15000))),
  recallLimit: Math.max(1, Math.floor(num(clientFile.recallLimit, 6))),
  scoreThreshold: Math.min(1, Math.max(0, num(clientFile.scoreThreshold, 0.01))),
};

// ---------------------------------------------------------------------------
// OpenViking HTTP Client (ported from openclaw-plugin/client.ts)
// ---------------------------------------------------------------------------

const MEMORY_URI_PATTERNS = [
  /^viking:\/\/user\/(?:[^/]+\/)?memories(?:\/|$)/,
  /^viking:\/\/agent\/(?:[^/]+\/)?memories(?:\/|$)/,
];
const USER_STRUCTURE_DIRS = new Set(["memories"]);
const AGENT_STRUCTURE_DIRS = new Set(["memories", "skills", "instructions", "workspaces"]);

function md5Short(input: string): string {
  return createHash("md5").update(input).digest("hex").slice(0, 12);
}

function isMemoryUri(uri: string): boolean {
  return MEMORY_URI_PATTERNS.some((p) => p.test(uri));
}

class OpenVikingClient {
  private resolvedSpaceByScope: Partial<Record<ScopeName, string>> = {};
  private runtimeIdentity: { userId: string; agentId: string } | null = null;

  constructor(
    private readonly baseUrl: string,
    private readonly apiKey: string,
    private readonly agentId: string,
    private readonly timeoutMs: number,
  ) {}

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const headers = new Headers(init.headers ?? {});
      if (this.apiKey) headers.set("X-API-Key", this.apiKey);
      if (this.agentId) headers.set("X-OpenViking-Agent", this.agentId);
      if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

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

  async healthCheck(): Promise<boolean> {
    try {
      await this.request<{ status: string }>("/health");
      return true;
    } catch {
      return false;
    }
  }

  private async ls(uri: string): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      `/api/v1/fs/ls?uri=${encodeURIComponent(uri)}&output=original`,
    );
  }

  private async getRuntimeIdentity(): Promise<{ userId: string; agentId: string }> {
    if (this.runtimeIdentity) return this.runtimeIdentity;
    const fallback = { userId: "default", agentId: this.agentId || "default" };
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
    if (cached) return cached;

    const identity = await this.getRuntimeIdentity();
    const fallbackSpace =
      scope === "user" ? identity.userId : md5Short(`${identity.userId}:${identity.agentId}`);
    const reservedDirs = scope === "user" ? USER_STRUCTURE_DIRS : AGENT_STRUCTURE_DIRS;

    try {
      const entries = await this.ls(`viking://${scope}`);
      const spaces = entries
        .filter((e) => e?.isDir === true)
        .map((e) => (typeof e.name === "string" ? e.name.trim() : ""))
        .filter((n) => n && !n.startsWith(".") && !reservedDirs.has(n));

      if (spaces.length > 0) {
        if (spaces.includes(fallbackSpace)) {
          this.resolvedSpaceByScope[scope] = fallbackSpace;
          return fallbackSpace;
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
    } catch { /* fall through */ }

    this.resolvedSpaceByScope[scope] = fallbackSpace;
    return fallbackSpace;
  }

  private async normalizeTargetUri(targetUri: string): Promise<string> {
    const trimmed = targetUri.trim().replace(/\/+$/, "");
    const match = trimmed.match(/^viking:\/\/(user|agent)(?:\/(.*))?$/);
    if (!match) return trimmed;

    const scope = match[1] as ScopeName;
    const rawRest = (match[2] ?? "").trim();
    if (!rawRest) return trimmed;

    const parts = rawRest.split("/").filter(Boolean);
    if (parts.length === 0) return trimmed;

    const reservedDirs = scope === "user" ? USER_STRUCTURE_DIRS : AGENT_STRUCTURE_DIRS;
    if (!reservedDirs.has(parts[0]!)) return trimmed;

    const space = await this.resolveScopeSpace(scope);
    return `viking://${scope}/${space}/${parts.join("/")}`;
  }

  async find(
    query: string,
    options: { targetUri: string; limit: number; scoreThreshold?: number },
  ): Promise<FindResult> {
    const normalizedTargetUri = await this.normalizeTargetUri(options.targetUri);
    return this.request<FindResult>("/api/v1/search/find", {
      method: "POST",
      body: JSON.stringify({
        query,
        target_uri: normalizedTargetUri,
        limit: options.limit,
        score_threshold: options.scoreThreshold,
      }),
    });
  }

  async read(uri: string): Promise<string> {
    return this.request<string>(`/api/v1/content/read?uri=${encodeURIComponent(uri)}`);
  }

  async createSession(): Promise<string> {
    const result = await this.request<{ session_id: string }>("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({}),
    });
    return result.session_id;
  }

  async addSessionMessage(sessionId: string, role: string, content: string): Promise<void> {
    await this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: "POST",
      body: JSON.stringify({ role, content }),
    });
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
}

// ---------------------------------------------------------------------------
// Memory ranking helpers (ported from openclaw-plugin/memory-ranking.ts)
// ---------------------------------------------------------------------------

function clampScore(value: number | undefined): number {
  if (typeof value !== "number" || Number.isNaN(value)) return 0;
  return Math.max(0, Math.min(1, value));
}

function normalizeDedupeText(text: string): string {
  return text.toLowerCase().replace(/\s+/g, " ").trim();
}

function getMemoryDedupeKey(item: FindResultItem): string {
  const abstract = normalizeDedupeText(item.abstract ?? item.overview ?? "");
  const category = (item.category ?? "").toLowerCase() || "unknown";
  if (abstract) return `abstract:${category}:${abstract}`;
  return `uri:${item.uri}`;
}

function postProcessMemories(
  items: FindResultItem[],
  options: { limit: number; scoreThreshold: number; leafOnly?: boolean },
): FindResultItem[] {
  const deduped: FindResultItem[] = [];
  const seen = new Set<string>();
  const sorted = [...items].sort((a, b) => clampScore(b.score) - clampScore(a.score));
  for (const item of sorted) {
    if (options.leafOnly && item.level !== 2) continue;
    if (clampScore(item.score) < options.scoreThreshold) continue;
    const key = getMemoryDedupeKey(item);
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(item);
    if (deduped.length >= options.limit) break;
  }
  return deduped;
}

function formatMemoryLines(items: FindResultItem[]): string {
  return items
    .map((item, i) => {
      const score = clampScore(item.score);
      const abstract = item.abstract?.trim() || item.overview?.trim() || item.uri;
      const category = item.category ?? "memory";
      return `${i + 1}. [${category}] ${abstract} (${(score * 100).toFixed(0)}%)`;
    })
    .join("\n");
}

// Query-aware ranking (ported from openclaw-plugin/memory-ranking.ts)
const PREFERENCE_QUERY_RE = /prefer|preference|favorite|favourite|like|偏好|喜欢|爱好|更倾向/i;
const TEMPORAL_QUERY_RE =
  /when|what time|date|day|month|year|yesterday|today|tomorrow|last|next|什么时候|何时|哪天|几月|几年|昨天|今天|明天/i;
const QUERY_TOKEN_RE = /[a-z0-9]{2,}/gi;
const QUERY_TOKEN_STOPWORDS = new Set([
  "what","when","where","which","who","whom","whose","why","how","did","does",
  "is","are","was","were","the","and","for","with","from","that","this","your","you",
]);

function buildQueryProfile(query: string) {
  const text = query.trim();
  const allTokens = text.toLowerCase().match(QUERY_TOKEN_RE) ?? [];
  const tokens = allTokens.filter((t) => !QUERY_TOKEN_STOPWORDS.has(t));
  return {
    tokens,
    wantsPreference: PREFERENCE_QUERY_RE.test(text),
    wantsTemporal: TEMPORAL_QUERY_RE.test(text),
  };
}

function lexicalOverlapBoost(tokens: string[], text: string): number {
  if (tokens.length === 0 || !text) return 0;
  const haystack = ` ${text.toLowerCase()} `;
  let matched = 0;
  for (const token of tokens.slice(0, 8)) {
    if (haystack.includes(token)) matched += 1;
  }
  return Math.min(0.2, (matched / Math.min(tokens.length, 4)) * 0.2);
}

function rankForInjection(item: FindResultItem, query: ReturnType<typeof buildQueryProfile>): number {
  const baseScore = clampScore(item.score);
  const abstract = (item.abstract ?? item.overview ?? "").trim();
  const leafBoost = item.level === 2 ? 0.12 : 0;
  const cat = (item.category ?? "").toLowerCase();
  const eventBoost = query.wantsTemporal && (cat === "events" || item.uri.includes("/events/")) ? 0.1 : 0;
  const prefBoost = query.wantsPreference && (cat === "preferences" || item.uri.includes("/preferences/")) ? 0.08 : 0;
  const overlapBoost = lexicalOverlapBoost(query.tokens, `${item.uri} ${abstract}`);
  return baseScore + leafBoost + eventBoost + prefBoost + overlapBoost;
}

function pickMemoriesForInjection(items: FindResultItem[], limit: number, queryText: string): FindResultItem[] {
  if (items.length === 0 || limit <= 0) return [];
  const query = buildQueryProfile(queryText);
  const sorted = [...items].sort((a, b) => rankForInjection(b, query) - rankForInjection(a, query));

  const deduped: FindResultItem[] = [];
  const seen = new Set<string>();
  for (const item of sorted) {
    const key = (item.abstract ?? item.overview ?? "").trim().toLowerCase() || item.uri;
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(item);
  }

  const leaves = deduped.filter((item) => item.level === 2);
  if (leaves.length >= limit) return leaves.slice(0, limit);

  const picked = [...leaves];
  const used = new Set(leaves.map((item) => item.uri));
  for (const item of deduped) {
    if (picked.length >= limit) break;
    if (used.has(item.uri)) continue;
    picked.push(item);
  }
  return picked;
}

// ---------------------------------------------------------------------------
// Shared search helpers
// ---------------------------------------------------------------------------

async function searchBothScopes(
  client: OpenVikingClient,
  query: string,
  limit: number,
): Promise<FindResultItem[]> {
  const [userSettled, agentSettled] = await Promise.allSettled([
    client.find(query, { targetUri: "viking://user/memories", limit, scoreThreshold: 0 }),
    client.find(query, { targetUri: "viking://agent/memories", limit, scoreThreshold: 0 }),
  ]);

  const userResult = userSettled.status === "fulfilled" ? userSettled.value : { memories: [] };
  const agentResult = agentSettled.status === "fulfilled" ? agentSettled.value : { memories: [] };

  const all = [...(userResult.memories ?? []), ...(agentResult.memories ?? [])];
  // Deduplicate by URI and keep only leaf memories
  const unique = all.filter((m, i, self) => i === self.findIndex((o) => o.uri === m.uri));
  return unique.filter((m) => m.level === 2);
}

// ---------------------------------------------------------------------------
// MCP Server
// ---------------------------------------------------------------------------

const client = new OpenVikingClient(config.baseUrl, config.apiKey, config.agentId, config.timeoutMs);

const server = new McpServer({
  name: "openviking-memory",
  version: "0.1.0",
});

// -- Tool: memory_recall --------------------------------------------------

server.tool(
  "memory_recall",
  "Search long-term memories from OpenViking. Use when you need past user preferences, facts, decisions, or any previously stored information.",
  {
    query: z.string().describe("Search query — describe what you want to recall"),
    limit: z.number().optional().describe("Max results to return (default: 6)"),
    score_threshold: z.number().optional().describe("Min relevance score 0-1 (default: 0.01)"),
    target_uri: z.string().optional().describe("Search scope URI, e.g. viking://user/memories"),
  },
  async ({ query, limit, score_threshold, target_uri }) => {
    const recallLimit = limit ?? config.recallLimit;
    const threshold = score_threshold ?? config.scoreThreshold;
    const candidateLimit = Math.max(recallLimit * 4, 20);

    let leafMemories: FindResultItem[];
    if (target_uri) {
      const result = await client.find(query, { targetUri: target_uri, limit: candidateLimit, scoreThreshold: 0 });
      leafMemories = (result.memories ?? []).filter((m) => m.level === 2);
    } else {
      leafMemories = await searchBothScopes(client, query, candidateLimit);
    }

    const processed = postProcessMemories(leafMemories, { limit: candidateLimit, scoreThreshold: threshold });
    const memories = pickMemoriesForInjection(processed, recallLimit, query);

    if (memories.length === 0) {
      return { content: [{ type: "text" as const, text: "No relevant memories found in OpenViking." }] };
    }

    // Read full content for leaf memories
    const lines = await Promise.all(
      memories.map(async (item) => {
        if (item.level === 2) {
          try {
            const content = await client.read(item.uri);
            if (content?.trim()) return `- [${item.category ?? "memory"}] ${content.trim()}`;
          } catch { /* fallback */ }
        }
        return `- [${item.category ?? "memory"}] ${item.abstract ?? item.uri}`;
      }),
    );

    return {
      content: [{
        type: "text" as const,
        text: `Found ${memories.length} relevant memories:\n\n${lines.join("\n")}\n\n---\n${formatMemoryLines(memories)}`,
      }],
    };
  },
);

// -- Tool: memory_store ---------------------------------------------------

server.tool(
  "memory_store",
  "Store information into OpenViking long-term memory. Use when the user says 'remember this', shares preferences, important facts, decisions, or any information worth persisting across sessions.",
  {
    text: z.string().describe("The information to store as memory"),
    role: z.string().optional().describe("Message role: 'user' (default) or 'assistant'"),
  },
  async ({ text, role }) => {
    const msgRole = role || "user";
    let sessionId: string | undefined;
    try {
      sessionId = await client.createSession();
      await client.addSessionMessage(sessionId, msgRole, text);
      const extracted = await client.extractSessionMemories(sessionId);

      if (extracted.length === 0) {
        return {
          content: [{
            type: "text" as const,
            text: "Memory stored but extraction returned 0 memories. The text may be too short or not contain extractable information. Check OpenViking server logs for details.",
          }],
        };
      }

      return {
        content: [{
          type: "text" as const,
          text: `Successfully extracted ${extracted.length} memory/memories from the provided text and stored them in OpenViking.`,
        }],
      };
    } finally {
      if (sessionId) {
        await client.deleteSession(sessionId).catch(() => {});
      }
    }
  },
);

// -- Tool: memory_forget --------------------------------------------------

server.tool(
  "memory_forget",
  "Delete a memory from OpenViking. Provide an exact URI for direct deletion, or a search query to find and delete matching memories.",
  {
    uri: z.string().optional().describe("Exact viking:// memory URI to delete"),
    query: z.string().optional().describe("Search query to find the memory to delete"),
    target_uri: z.string().optional().describe("Search scope URI (default: viking://user/memories)"),
  },
  async ({ uri, query, target_uri }) => {
    // Direct URI deletion
    if (uri) {
      if (!isMemoryUri(uri)) {
        return { content: [{ type: "text" as const, text: `Refusing to delete non-memory URI: ${uri}` }] };
      }
      await client.deleteUri(uri);
      return { content: [{ type: "text" as const, text: `Deleted memory: ${uri}` }] };
    }

    if (!query) {
      return { content: [{ type: "text" as const, text: "Please provide either a uri or query parameter." }] };
    }

    // Search then delete
    const candidateLimit = 20;
    let candidates: FindResultItem[];

    if (target_uri) {
      const result = await client.find(query, { targetUri: target_uri, limit: candidateLimit, scoreThreshold: 0 });
      candidates = postProcessMemories(result.memories ?? [], {
        limit: candidateLimit,
        scoreThreshold: config.scoreThreshold,
        leafOnly: true,
      }).filter((item) => isMemoryUri(item.uri));
    } else {
      const leafMemories = await searchBothScopes(client, query, candidateLimit);
      candidates = postProcessMemories(leafMemories, {
        limit: candidateLimit,
        scoreThreshold: config.scoreThreshold,
        leafOnly: true,
      }).filter((item) => isMemoryUri(item.uri));
    }

    if (candidates.length === 0) {
      return { content: [{ type: "text" as const, text: "No matching memories found. Try a more specific query." }] };
    }

    // Auto-delete if single strong match
    const top = candidates[0]!;
    if (candidates.length === 1 && clampScore(top.score) >= 0.85) {
      await client.deleteUri(top.uri);
      return { content: [{ type: "text" as const, text: `Deleted memory: ${top.uri}` }] };
    }

    // List candidates for confirmation
    const list = candidates
      .map((item) => `- ${item.uri} — ${item.abstract?.trim() || "?"} (${(clampScore(item.score) * 100).toFixed(0)}%)`)
      .join("\n");

    return {
      content: [{
        type: "text" as const,
        text: `Found ${candidates.length} candidate memories. Please specify the exact URI to delete:\n\n${list}`,
      }],
    };
  },
);

// -- Tool: memory_health --------------------------------------------------

server.tool(
  "memory_health",
  "Check whether the OpenViking memory server is reachable and healthy.",
  {},
  async () => {
    const ok = await client.healthCheck();
    return {
      content: [{
        type: "text" as const,
        text: ok
          ? `OpenViking is healthy (${config.baseUrl})`
          : `OpenViking is unreachable at ${config.baseUrl}. Please check if the server is running.`,
      }],
    };
  },
);

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

const transport = new StdioServerTransport();
await server.connect(transport);
