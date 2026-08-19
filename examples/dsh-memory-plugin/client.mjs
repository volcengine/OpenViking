export class OpenVikingClient {
  constructor(config) {
    this.config = config;
    this.connected = false;
  }

  headers(options = {}) {
    const headers = { "Content-Type": "application/json" };
    if (this.config.apiKey) headers.Authorization = `Bearer ${this.config.apiKey}`;
    if (this.config.account) headers["X-OpenViking-Account"] = this.config.account;
    if (this.config.user) headers["X-OpenViking-User"] = this.config.user;
    const actorPeerId = options.actorPeerId ?? this.config.peerId;
    if (actorPeerId) headers["X-OpenViking-Actor-Peer"] = actorPeerId;
    if (this.config.userAgent) headers["User-Agent"] = this.config.userAgent;
    return headers;
  }

  async fetchJSON(path, init = {}, options = {}) {
    const timeoutMs = options.timeoutMs ?? this.config.requestTimeoutMs;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(`${this.config.endpoint}${path}`, {
        ...init,
        headers: {
          ...this.headers(options),
          ...(init.headers || {}),
        },
        signal: controller.signal,
      });
      const body = await response.json().catch(() => ({}));
      const traceId = body?.result?.trace_id
        || body?.error?.trace_id
        || body?.trace_id
        || undefined;
      if (!response.ok || body?.status === "error") {
        return {
          ok: false,
          result: null,
          status: response.status,
          error: body?.error || { message: `HTTP ${response.status}` },
          traceId,
        };
      }
      return {
        ok: true,
        result: body?.result ?? body,
        status: response.status,
        traceId,
      };
    } catch (error) {
      return {
        ok: false,
        result: null,
        status: 0,
        error: { message: error instanceof Error ? error.message : String(error) },
      };
    } finally {
      clearTimeout(timer);
    }
  }

  async health() {
    return (await this.healthResult()).ok;
  }

  async healthResult() {
    const response = await this.fetchJSON("/health", {}, { timeoutMs: 5000 });
    this.connected = response.ok;
    return response;
  }

  async ensureSession(sessionId, actorPeerId) {
    const response = await this.ensureSessionResult(sessionId, actorPeerId);
    return response.ok
      || (response.status === 409 && response.error?.code === "ALREADY_EXISTS");
  }

  async ensureSessionResult(sessionId, actorPeerId) {
    const response = await this.fetchJSON("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId }),
    }, { actorPeerId });
    return response;
  }

  async getSession(sessionId, actorPeerId) {
    const response = await this.fetchJSON(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}`,
      {},
      { timeoutMs: 5000, actorPeerId },
    );
    return response.ok ? response.result : null;
  }

  async getSessionArchive(sessionId, archiveId, actorPeerId) {
    const response = await this.fetchJSON(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/archives/${encodeURIComponent(archiveId)}`,
      {},
      { actorPeerId },
    );
    return response.ok ? response.result : null;
  }

  async addMessage(sessionId, payload, actorPeerId) {
    return this.fetchJSON(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
      { actorPeerId },
    );
  }

  async commitSession(sessionId, actorPeerId, options = {}) {
    return this.fetchJSON(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`,
      {
        method: "POST",
        body: JSON.stringify({ keep_recent_count: this.config.commitKeepRecentCount }),
      },
      { timeoutMs: options.timeoutMs ?? 30000, actorPeerId },
    );
  }

  async find(query, options = {}) {
    const body = { query };
    if (options.targetUri) body.target_uri = options.targetUri;
    if (options.limit) body.limit = options.limit;
    if (options.scoreThreshold !== undefined) body.score_threshold = options.scoreThreshold;
    const response = await this.fetchJSON("/api/v1/search/find", {
      method: "POST",
      body: JSON.stringify(body),
    }, { actorPeerId: options.actorPeerId });
    if (!response.ok || !response.result) return [];

    const results = [];
    for (const bucket of ["memories", "resources", "skills"]) {
      const entries = response.result[bucket];
      if (!Array.isArray(entries)) continue;
      for (const entry of entries) {
        results.push({
          uri: entry?.uri || "",
          contextType: entry?.context_type
            || (bucket === "memories" ? "memory" : bucket === "skills" ? "skill" : "resource"),
          score: Number(entry?.score || 0),
          abstract: entry?.abstract || "",
          overview: entry?.overview || null,
        });
      }
    }
    return results;
  }

  async read(uri, level, actorPeerId) {
    const endpoint = level === "abstract"
      ? "abstract"
      : level === "overview"
        ? "overview"
        : "read";
    const response = await this.fetchJSON(
      `/api/v1/content/${endpoint}?uri=${encodeURIComponent(uri)}`,
      {},
      { actorPeerId },
    );
    return response.ok ? response.result : null;
  }

  async list(uri, actorPeerId) {
    const response = await this.fetchJSON(
      `/api/v1/fs/ls?uri=${encodeURIComponent(uri)}&output=original`,
      {},
      { actorPeerId },
    );
    return response.ok && Array.isArray(response.result) ? response.result : [];
  }

  async stat(uri, actorPeerId) {
    const response = await this.fetchJSON(
      `/api/v1/fs/stat?uri=${encodeURIComponent(uri)}`,
      {},
      { actorPeerId },
    );
    return response.ok ? response.result : null;
  }

  async forget(uri, recursive = false, actorPeerId) {
    const response = await this.fetchJSON(
      `/api/v1/fs?uri=${encodeURIComponent(uri)}&recursive=${recursive}`,
      { method: "DELETE" },
      { actorPeerId },
    );
    return response.ok;
  }

  async addResource(path, reason, actorPeerId) {
    const body = { path };
    if (reason) body.reason = reason;
    const response = await this.fetchJSON("/api/v1/resources", {
      method: "POST",
      body: JSON.stringify(body),
    }, { timeoutMs: 30000, actorPeerId });
    return response.ok ? response.result : null;
  }
}
