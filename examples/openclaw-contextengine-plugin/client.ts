type FindOptions = {
  targetUri?: string;
  limit?: number;
  scoreThreshold?: number;
};

type ClientOptions = {
  baseUrl: string;
  timeoutMs: number;
  apiKey?: string;
  agentId?: string;
};

type OpenVikingFindResult = {
  memories?: Array<{
    uri: string;
    score?: number;
    content?: string;
    level?: number;
  }>;
  total?: number;
};

export type OpenVikingClient = {
  baseUrl: string;
  health: () => Promise<boolean>;
  find: (query: string, opts?: FindOptions) => Promise<OpenVikingFindResult>;
  createSession: () => Promise<string>;
  addSessionMessage: (sessionId: string, role: string, content: string) => Promise<void>;
  commitSession: (sessionId: string) => Promise<{ extractedCount: number }>;
  deleteSession: (sessionId: string) => Promise<void>;
};

export function createOpenVikingClient(options: ClientOptions): OpenVikingClient {
  const baseUrl = options.baseUrl.replace(/\/+$/, "");

  const request = async <T>(path: string, init?: RequestInit): Promise<T> => {
    const headers = new Headers(init?.headers);
    if (!headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (options.apiKey) {
      headers.set("Authorization", `Bearer ${options.apiKey}`);
    }
    if (options.agentId) {
      headers.set("X-OpenViking-Agent", options.agentId);
    }

    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort();
    }, options.timeoutMs);

    try {
      const response = await fetch(`${baseUrl}${path}`, {
        ...init,
        headers,
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`OpenViking request failed (${response.status}) on ${path}`);
      }

      if (response.status === 204) {
        return undefined as T;
      }

      const payload = (await response.json()) as unknown;
      if (payload && typeof payload === "object" && !Array.isArray(payload)) {
        const envelope = payload as {
          status?: unknown;
          result?: unknown;
          error?: unknown;
        };

        if (typeof envelope.status === "string" && envelope.status !== "ok") {
          const message =
            typeof envelope.error === "string"
              ? envelope.error
              : `OpenViking request returned status ${envelope.status} on ${path}`;
          throw new Error(message);
        }

        if (Object.prototype.hasOwnProperty.call(envelope, "result")) {
          return envelope.result as T;
        }
      }

      return payload as T;
    } catch (error) {
      if (controller.signal.aborted) {
        throw new Error(`request timeout after ${options.timeoutMs}ms`);
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
  };

  return {
    baseUrl,
    async health() {
      try {
        const data = await request<{ status?: string }>("/health", { method: "GET" });
        return data?.status === "ok";
      } catch {
        return false;
      }
    },
    find(query, opts = {}) {
      return request<OpenVikingFindResult>("/api/v1/search/find", {
        method: "POST",
        body: JSON.stringify({
          query,
          target_uri: opts.targetUri ?? "",
          limit: opts.limit ?? 10,
          score_threshold: opts.scoreThreshold,
        }),
      });
    },
    async createSession() {
      const data = await request<{ session_id?: string; id?: string }>("/api/v1/sessions", {
        method: "POST",
        body: JSON.stringify({}),
      });
      const sessionId = data.session_id ?? data.id;
      if (!sessionId) {
        throw new Error("OpenViking createSession returned no session id");
      }
      return sessionId;
    },
    async addSessionMessage(sessionId, role, content) {
      await request(`/api/v1/sessions/${sessionId}/messages`, {
        method: "POST",
        body: JSON.stringify({ role, content }),
      });
    },
    async commitSession(sessionId) {
      const data = await request<{ extractedCount?: number; extracted_count?: number }>(
        `/api/v1/sessions/${sessionId}/commit`,
        { method: "POST" },
      );
      return {
        extractedCount: data.extractedCount ?? data.extracted_count ?? 0,
      };
    },
    async deleteSession(sessionId) {
      await request(`/api/v1/sessions/${sessionId}`, {
        method: "DELETE",
      });
    },
  };
}
