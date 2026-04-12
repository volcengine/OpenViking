/**
 * Shared OpenViking HTTP client for hook scripts.
 * Provides session management, search, and content read methods.
 */

export function createClient(config) {
  const { baseUrl, apiKey, account, user, agentId, timeoutMs, captureTimeoutMs } = config;

  function headers() {
    const h = { "Content-Type": "application/json" };
    if (apiKey) h["X-API-Key"] = apiKey;
    if (account) h["X-OpenViking-Account"] = account;
    if (user) h["X-OpenViking-User"] = user;
    if (agentId) h["X-OpenViking-Agent"] = agentId;
    return h;
  }

  async function request(path, init = {}, timeout = timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const res = await fetch(`${baseUrl}${path}`, {
        ...init,
        headers: { ...headers(), ...(init.headers || {}) },
        signal: controller.signal,
      });
      const body = await res.json();
      if (!res.ok || body.status === "error") return null;
      return body.result ?? body;
    } catch {
      return null;
    } finally {
      clearTimeout(timer);
    }
  }

  // URI space resolution (mirrors MCP server normalizeTargetUri logic)
  const USER_RESERVED = new Set(["memories"]);
  const AGENT_RESERVED = new Set(["memories", "skills", "instructions", "workspaces"]);
  const spaceCache = {};

  async function resolveScopeSpace(scope) {
    if (spaceCache[scope]) return spaceCache[scope];
    let fallbackSpace = "default";
    try {
      const status = await request("/api/v1/system/status");
      if (status && typeof status.user === "string" && status.user.trim()) {
        fallbackSpace = status.user.trim();
      }
    } catch { /* use fallback */ }
    const reserved = scope === "user" ? USER_RESERVED : AGENT_RESERVED;
    try {
      const entries = await request(`/api/v1/fs/ls?uri=${encodeURIComponent(`viking://${scope}`)}&output=original`);
      if (Array.isArray(entries)) {
        const spaces = entries
          .filter(e => e?.isDir)
          .map(e => (typeof e.name === "string" ? e.name.trim() : ""))
          .filter(n => n && !n.startsWith(".") && !reserved.has(n));
        if (spaces.length > 0) {
          if (spaces.includes(fallbackSpace)) { spaceCache[scope] = fallbackSpace; return fallbackSpace; }
          if (scope === "user" && spaces.includes("default")) { spaceCache[scope] = "default"; return "default"; }
          if (spaces.length === 1) { spaceCache[scope] = spaces[0]; return spaces[0]; }
        }
      }
    } catch { /* use fallback */ }
    spaceCache[scope] = fallbackSpace;
    return fallbackSpace;
  }

  async function resolveTargetUri(targetUri) {
    const trimmed = targetUri.trim().replace(/\/+$/, "");
    const m = trimmed.match(/^viking:\/\/(user|agent)(?:\/(.*))?$/);
    if (!m) return trimmed;
    const scope = m[1];
    const rawRest = (m[2] ?? "").trim();
    if (!rawRest) return trimmed;
    const parts = rawRest.split("/").filter(Boolean);
    if (parts.length === 0) return trimmed;
    const reserved = scope === "user" ? USER_RESERVED : AGENT_RESERVED;
    if (!reserved.has(parts[0])) return trimmed;
    const space = await resolveScopeSpace(scope);
    return `viking://${scope}/${space}/${parts.join("/")}`;
  }

  return {
    async healthCheck() {
      const result = await request("/health");
      return !!result;
    },

    async find(query, targetUri, limit = 24) {
      const resolved = await resolveTargetUri(targetUri);
      const result = await request("/api/v1/search/find", {
        method: "POST",
        body: JSON.stringify({ query, target_uri: resolved, limit, score_threshold: 0 }),
      });
      return result?.memories || [];
    },

    async read(uri) {
      const result = await request(`/api/v1/content/read?uri=${encodeURIComponent(uri)}`);
      if (result && typeof result === "string" && result.trim()) return result.trim();
      return null;
    },

    async createSession(sessionId) {
      const body = sessionId ? { session_id: sessionId } : {};
      const result = await request("/api/v1/sessions", {
        method: "POST",
        body: JSON.stringify(body),
      }, captureTimeoutMs);
      return result?.session_id || null;
    },

    async getSession(sessionId) {
      return await request(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, {}, captureTimeoutMs);
    },

    async addSessionMessage(sessionId, role, content) {
      return await request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`, {
        method: "POST",
        body: JSON.stringify({ role, content }),
      }, captureTimeoutMs);
    },

    async commitSession(sessionId) {
      return await request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`, {
        method: "POST",
        body: JSON.stringify({}),
      }, captureTimeoutMs);
    },

    async getSessionContext(sessionId, tokenBudget = 128000) {
      return await request(
        `/api/v1/sessions/${encodeURIComponent(sessionId)}/context?token_budget=${tokenBudget}`,
        {},
        captureTimeoutMs,
      );
    },

    async getSessionArchive(sessionId, archiveId) {
      return await request(
        `/api/v1/sessions/${encodeURIComponent(sessionId)}/archives/${encodeURIComponent(archiveId)}`,
        {},
        captureTimeoutMs,
      );
    },

    async extractSessionMemories(sessionId) {
      return await request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/extract`, {
        method: "POST",
        body: JSON.stringify({}),
      }, captureTimeoutMs);
    },

    async deleteSession(sessionId) {
      return await request(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, {
        method: "DELETE",
      }, captureTimeoutMs);
    },

    async deleteUri(uri) {
      return await request(`/api/v1/fs?uri=${encodeURIComponent(uri)}&recursive=false`, {
        method: "DELETE",
      });
    },

    async getTask(taskId) {
      return await request(`/api/v1/tasks/${encodeURIComponent(taskId)}`);
    },
  };
}
