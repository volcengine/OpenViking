import { createOvHttp } from "./shared/ov-http.mjs";

export class OpenVikingClient {
  constructor(config) {
    this.config = config;
    this.connected = false;
    this.fetchJSON = createOvHttp(config, {
      defaultTimeoutMs: config.requestTimeoutMs,
      resolveActorPeerId: () => this.config.peerId,
    });
  }

  async healthResult() {
    const response = await this.fetchJSON("/health", {}, { timeoutMs: 5000 });
    this.connected = response.ok;
    return response;
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
}
