import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PluginConfig } from "../config.js";
import { OVClient } from "../ov-client.js";

interface FetchCall {
  url: string;
  method: string;
  headers: Record<string, string>;
  body?: unknown;
  signal?: AbortSignal;
}

interface MockOptions {
  /** Sequence of responses, one per call. Reuses the last item once exhausted. */
  responses: Array<{ status?: number; body?: unknown; throwError?: Error; delayMs?: number }>;
}

function makeMockFetch(opts: MockOptions): { calls: FetchCall[]; impl: typeof fetch } {
  const calls: FetchCall[] = [];
  let i = 0;

  const impl = (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : (input as Request).url;
    const method = (init?.method ?? "GET").toUpperCase();
    const headers: Record<string, string> = {};
    if (init?.headers) {
      for (const [k, v] of Object.entries(init.headers as Record<string, string>)) {
        headers[k] = v;
      }
    }
    let parsedBody: unknown;
    if (init?.body) {
      try { parsedBody = JSON.parse(String(init.body)); } catch { parsedBody = init.body; }
    }
    calls.push({ url, method, headers, body: parsedBody, signal: init?.signal ?? undefined });

    const idx = Math.min(i, opts.responses.length - 1);
    const resp = opts.responses[idx]!;
    i++;

    if (resp.delayMs) {
      await new Promise((resolve, reject) => {
        const t = setTimeout(resolve, resp.delayMs);
        init?.signal?.addEventListener("abort", () => {
          clearTimeout(t);
          reject(new DOMException("aborted", "AbortError"));
        });
      });
    }
    if (resp.throwError) throw resp.throwError;

    const status = resp.status ?? 200;
    const ok = status >= 200 && status < 300;
    return {
      ok,
      status,
      json: async () => resp.body ?? {},
    } as unknown as Response;
  }) as unknown as typeof fetch;

  return { calls, impl };
}

function baseCfg(overrides: Partial<PluginConfig> = {}): PluginConfig {
  return {
    configPath: null,
    baseUrl: "http://ov.test",
    apiKey: "test-api-key",
    agentId: "copilot-vscode",
    accountId: "team-acme",
    userId: "alice",
    timeoutMs: 5000,
    autoRecall: true,
    recallLimit: 6,
    scoreThreshold: 0.35,
    minQueryLength: 3,
    logRankingDetails: false,
    recallMaxContentChars: 500,
    recallTokenBudget: 2000,
    recallPreferAbstract: true,
    autoCapture: true,
    captureMode: "semantic",
    captureMaxLength: 24000,
    captureTimeoutMs: 30000,
    captureAssistantTurns: true,
    commitTokenThreshold: 20000,
    resumeContextBudget: 32000,
    bypassSession: false,
    bypassSessionPatterns: [],
    writePathAsync: true,
    debug: false,
    debugLogPath: "/tmp/test.log",
    ...overrides,
  };
}

describe("OVClient — header injection", () => {
  it("sends Authorization + tenant headers when cfg has them", async () => {
    const { calls, impl } = makeMockFetch({ responses: [{ body: { result: { ok: true } } }] });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    await client.health();

    expect(calls).toHaveLength(1);
    const h = calls[0]!.headers;
    expect(h["Content-Type"]).toBe("application/json");
    expect(h["Authorization"]).toBe("Bearer test-api-key");
    expect(h["X-OpenViking-Account"]).toBe("team-acme");
    expect(h["X-OpenViking-User"]).toBe("alice");
    expect(h["X-OpenViking-Agent"]).toBe("copilot-vscode");
  });

  it("omits Authorization + tenant headers when cfg fields are empty", async () => {
    const { calls, impl } = makeMockFetch({ responses: [{ body: { result: {} } }] });
    const client = new OVClient(
      baseCfg({ apiKey: "", accountId: "", userId: "", agentId: "" }),
      { fetchImpl: impl },
    );
    await client.health();
    const h = calls[0]!.headers;
    expect(h["Authorization"]).toBeUndefined();
    expect(h["X-OpenViking-Account"]).toBeUndefined();
    expect(h["X-OpenViking-User"]).toBeUndefined();
    expect(h["X-OpenViking-Agent"]).toBeUndefined();
  });
});

describe("OVClient — endpoint shapes", () => {
  it("health hits GET /health", async () => {
    const { calls, impl } = makeMockFetch({ responses: [{ body: { ok: true } }] });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    const res = await client.health();
    expect(res.ok).toBe(true);
    expect(calls[0]!.method).toBe("GET");
    expect(calls[0]!.url).toBe("http://ov.test/health");
    expect(calls[0]!.body).toBeUndefined();
  });

  it("recall hits POST /api/v1/search/find with the right body and flattens buckets", async () => {
    const { calls, impl } = makeMockFetch({
      responses: [{
        body: {
          result: {
            memories: [{ uri: "viking://m/1", score: 0.9 }],
            skills: [{ uri: "viking://s/1", score: 0.7 }],
          },
        },
      }],
    });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    const res = await client.recall("auth migration", {
      limit: 4,
      sessionId: "cp-abc",
      targetUri: "viking://agent/memories",
      scoreThreshold: 0.2,
    });
    expect(res.ok).toBe(true);
    if (!res.ok) throw new Error();
    expect(res.value).toHaveLength(2);
    expect(res.value.map((h) => h.uri)).toEqual(["viking://m/1", "viking://s/1"]);

    expect(calls[0]!.method).toBe("POST");
    expect(calls[0]!.url).toBe("http://ov.test/api/v1/search/find");
    expect(calls[0]!.body).toEqual({
      query: "auth migration",
      limit: 4,
      score_threshold: 0.2,
      target_uri: "viking://agent/memories",
      session_id: "cp-abc",
    });
  });

  it("recall stamps the bucket name (singularised) onto each hit as `type`", async () => {
    const { impl } = makeMockFetch({
      responses: [{
        body: {
          result: {
            memories: [{ uri: "viking://m/1", score: 0.9 }],
            skills: [{ uri: "viking://s/1", score: 0.6 }],
            resources: [{ uri: "viking://r/1", score: 0.5 }],
          },
        },
      }],
    });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    const res = await client.recall("q", { limit: 5, sessionId: "cp-z" });
    expect(res.ok).toBe(true);
    if (!res.ok) throw new Error();
    const byUri = Object.fromEntries(res.value.map((h) => [h.uri, h.type]));
    expect(byUri["viking://m/1"]).toBe("memory");
    expect(byUri["viking://s/1"]).toBe("skill");
    expect(byUri["viking://r/1"]).toBe("resource");
  });

  it("recall preserves a server-set `type` rather than overwriting with the bucket name", async () => {
    const { impl } = makeMockFetch({
      responses: [{
        body: { result: { memories: [{ uri: "viking://m/1", score: 0.9, type: "custom-type" }] } },
      }],
    });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    const res = await client.recall("q", { limit: 1, sessionId: "cp-z" });
    expect(res.ok).toBe(true);
    if (!res.ok) throw new Error();
    expect(res.value[0]!.type).toBe("custom-type");
  });

  it("appendTurns POSTs each turn to /sessions/{id}/messages and reports the count", async () => {
    const { calls, impl } = makeMockFetch({ responses: [{ body: { result: {} } }] });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    const res = await client.appendTurns("cp-zzz", [
      { role: "user", content: "first" },
      { role: "assistant", content: "second" },
    ]);
    expect(res.ok).toBe(true);
    if (!res.ok) throw new Error();
    expect(res.value).toEqual({ written: 2 });

    expect(calls).toHaveLength(2);
    expect(calls[0]!.url).toBe("http://ov.test/api/v1/sessions/cp-zzz/messages");
    expect(calls[0]!.body).toEqual({ role: "user", content: "first" });
    expect(calls[1]!.body).toEqual({ role: "assistant", content: "second" });
  });

  it("appendTurns is a no-op when the turns array is empty", async () => {
    const { calls, impl } = makeMockFetch({ responses: [{ body: {} }] });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    const res = await client.appendTurns("cp-zzz", []);
    expect(res.ok).toBe(true);
    expect(calls).toHaveLength(0);
  });

  it("appendTurns stops at the first failure and returns the error", async () => {
    const { calls, impl } = makeMockFetch({
      responses: [
        { body: { result: {} } },
        { status: 500, body: { error: { message: "boom" } } },
      ],
    });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    const res = await client.appendTurns("cp-zzz", [
      { role: "user", content: "ok" },
      { role: "assistant", content: "fails" },
      { role: "user", content: "never sent" },
    ]);
    expect(res.ok).toBe(false);
    if (res.ok) throw new Error();
    expect(res.error.status).toBe(500);
    expect(res.error.message).toBe("boom");
    expect(calls).toHaveLength(2);
  });

  it("commit POSTs to /sessions/{id}/commit with empty body by default and {force:true} on demand", async () => {
    const { calls, impl } = makeMockFetch({ responses: [{ body: { result: {} } }, { body: { result: {} } }] });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    await client.commit("cp-zzz");
    await client.commit("cp-zzz", { force: true });

    expect(calls[0]!.method).toBe("POST");
    expect(calls[0]!.url).toBe("http://ov.test/api/v1/sessions/cp-zzz/commit");
    expect(calls[0]!.body).toEqual({});
    expect(calls[1]!.body).toEqual({ force: true });
  });

  it("read GETs /api/v1/content/read with URL-encoded uri and returns text", async () => {
    const { calls, impl } = makeMockFetch({ responses: [{ body: { result: "hello memory" } }] });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    const res = await client.read("viking://m/1", { offset: 5, limit: 20 });
    expect(res.ok).toBe(true);
    if (!res.ok) throw new Error();
    expect(res.value).toBe("hello memory");
    expect(calls[0]!.method).toBe("GET");
    expect(calls[0]!.url).toBe("http://ov.test/api/v1/content/read?uri=viking%3A%2F%2Fm%2F1&offset=5&limit=20");
  });

  it("read serialises object and array results as formatted JSON text", async () => {
    const { impl } = makeMockFetch({ responses: [{ body: { result: { uri: "viking://m/1", content: ["a"] } } }] });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    const res = await client.read("viking://m/1");
    expect(res.ok).toBe(true);
    if (!res.ok) throw new Error();
    expect(res.value).toBe(JSON.stringify({ uri: "viking://m/1", content: ["a"] }, null, 2));
  });

  it("fetchArchiveOverview returns latest_archive_overview when present", async () => {
    const { calls, impl } = makeMockFetch({
      responses: [{ body: { result: { latest_archive_overview: "summary text" } } }],
    });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    const res = await client.fetchArchiveOverview("cp-zzz", 10000);
    expect(res.ok).toBe(true);
    if (!res.ok) throw new Error();
    expect(res.value).toBe("summary text");
    expect(calls[0]!.method).toBe("GET");
    expect(calls[0]!.url).toBe(
      "http://ov.test/api/v1/sessions/cp-zzz/context?token_budget=10000",
    );
  });

  it("fetchArchiveOverview maps 404 to ok:true with null overview", async () => {
    const { impl } = makeMockFetch({ responses: [{ status: 404, body: { error: { message: "not found" } } }] });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    const res = await client.fetchArchiveOverview("cp-missing", 10000);
    expect(res.ok).toBe(true);
    if (!res.ok) throw new Error();
    expect(res.value).toBeNull();
  });

  it("URL-encodes the session id segment", async () => {
    const { calls, impl } = makeMockFetch({ responses: [{ body: { result: {} } }] });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    await client.commit("cc-with/slash");
    expect(calls[0]!.url).toBe("http://ov.test/api/v1/sessions/cc-with%2Fslash/commit");
  });

  it("forget DELETEs /api/v1/fs?uri=... with URL-encoded uri", async () => {
    const { calls, impl } = makeMockFetch({ responses: [{ body: { result: { uri: "viking://m/1" } } }] });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    const res = await client.forget("viking://m/1");
    expect(res.ok).toBe(true);
    expect(calls[0]!.method).toBe("DELETE");
    expect(calls[0]!.url).toBe("http://ov.test/api/v1/fs?uri=viking%3A%2F%2Fm%2F1");
  });

  it("forget(uri, {recursive:true}) appends &recursive=true", async () => {
    const { calls, impl } = makeMockFetch({ responses: [{ body: { result: {} } }] });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    await client.forget("viking://dir/path", { recursive: true });
    expect(calls[0]!.url).toBe("http://ov.test/api/v1/fs?uri=viking%3A%2F%2Fdir%2Fpath&recursive=true");
  });

  it("forget short-circuits when bypass is active", async () => {
    const { calls, impl } = makeMockFetch({ responses: [{ body: {} }] });
    const client = new OVClient(baseCfg({ bypassSession: true }), { fetchImpl: impl });
    const res = await client.forget("viking://x");
    expect(res.ok).toBe(true);
    expect(calls).toHaveLength(0);
  });
});

describe("OVClient — error mapping", () => {
  it("maps body.status === 'error' to ok:false even when HTTP is 200", async () => {
    const { impl } = makeMockFetch({
      responses: [{ status: 200, body: { status: "error", error: { message: "bad query" } } }],
    });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    const res = await client.recall("x", { limit: 1, sessionId: "cp-z" });
    expect(res.ok).toBe(false);
    if (res.ok) throw new Error();
    expect(res.error.message).toBe("bad query");
  });

  it("maps a non-2xx with no body.error.message to a synthesised HTTP message", async () => {
    const { impl } = makeMockFetch({ responses: [{ status: 503, body: {} }] });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    const res = await client.health();
    expect(res.ok).toBe(false);
    if (res.ok) throw new Error();
    expect(res.error.message).toBe("HTTP 503");
    expect(res.error.status).toBe(503);
  });

  it("maps a thrown fetch error to ok:false with the error message", async () => {
    const { impl } = makeMockFetch({ responses: [{ throwError: new Error("ECONNREFUSED") }] });
    const client = new OVClient(baseCfg(), { fetchImpl: impl });
    const res = await client.health();
    expect(res.ok).toBe(false);
    if (res.ok) throw new Error();
    expect(res.error.message).toBe("ECONNREFUSED");
  });
});

describe("OVClient — timeout", () => {
  beforeEach(() => { vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] }); });
  afterEach(() => { vi.useRealTimers(); });

  it("aborts the request after timeoutMs and surfaces an error", async () => {
    const { impl } = makeMockFetch({ responses: [{ delayMs: 5000, body: {} }] });
    const client = new OVClient(baseCfg({ timeoutMs: 1000 }), { fetchImpl: impl });
    const promise = client.health();
    await vi.advanceTimersByTimeAsync(1500);
    const res = await promise;
    expect(res.ok).toBe(false);
  });
});

describe("OVClient — bypass", () => {
  it("bypassSession=true short-circuits every method", async () => {
    const { calls, impl } = makeMockFetch({ responses: [{ body: {} }] });
    const client = new OVClient(baseCfg({ bypassSession: true }), { fetchImpl: impl });
    const h = await client.health();
    const r = await client.recall("q", { limit: 3, sessionId: "cp-z" });
    const read = await client.read("viking://m/1");
    const a = await client.appendTurns("cp-z", [{ role: "user", content: "x" }]);
    const c = await client.commit("cp-z");
    const o = await client.fetchArchiveOverview("cp-z", 1000);

    expect(h.ok).toBe(true);
    expect(r.ok && r.value).toEqual([]);
    expect(read.ok && read.value).toBe("");
    expect(a.ok).toBe(true);
    expect(c.ok).toBe(true);
    expect(o.ok && o.value).toBe(null);
    expect(calls).toHaveLength(0);
  });

  it("bypassSessionPatterns matches against bypassContext.cwd", async () => {
    const { calls, impl } = makeMockFetch({ responses: [{ body: {} }] });
    const client = new OVClient(
      baseCfg({ bypassSessionPatterns: ["/tmp/**", "**/scratch/**"] }),
      { fetchImpl: impl, bypassContext: { cwd: "/tmp/throwaway-xyz" } },
    );
    expect(client.isBypassed()).toBe(true);
    await client.health();
    expect(calls).toHaveLength(0);
  });

  it("bypassSessionPatterns matches against bypassContext.hostSessionId", async () => {
    const { calls, impl } = makeMockFetch({ responses: [{ body: {} }] });
    const client = new OVClient(
      baseCfg({ bypassSessionPatterns: ["scratch-*"] }),
      { fetchImpl: impl, bypassContext: { hostSessionId: "scratch-2026" } },
    );
    expect(client.isBypassed()).toBe(true);
    await client.recall("q", { limit: 1, sessionId: "cp-z" });
    expect(calls).toHaveLength(0);
  });

  it("does NOT bypass when neither cwd nor hostSessionId match", async () => {
    const { calls, impl } = makeMockFetch({ responses: [{ body: { result: {} } }] });
    const client = new OVClient(
      baseCfg({ bypassSessionPatterns: ["/tmp/**"] }),
      { fetchImpl: impl, bypassContext: { cwd: "/Users/me/work" } },
    );
    expect(client.isBypassed()).toBe(false);
    await client.health();
    expect(calls).toHaveLength(1);
  });
});

describe("OVClient — construction", () => {
  it("throws when fetch is unavailable and no fetchImpl provided", () => {
    const originalFetch = (globalThis as { fetch?: typeof fetch }).fetch;
    delete (globalThis as { fetch?: typeof fetch }).fetch;
    try {
      expect(() => new OVClient(baseCfg())).toThrow(/fetch/);
    } finally {
      if (originalFetch) (globalThis as { fetch?: typeof fetch }).fetch = originalFetch;
    }
  });
});
