import { describe, expect, it, vi } from "vitest";
import {
  CommitQueue,
  OVClient,
  type OVResult,
  type OVTurn,
  type PluginConfig,
  RecallCache,
  type RecallHit,
  createDebugLogger,
} from "@openviking/copilot-shared";
import {
  buildRecallContext,
  runForget,
  runStore,
  type ParticipantState,
} from "../participant-core";

const SESSION_ID = "cp-test-participant";

interface MockResponse {
  status?: number;
  body?: unknown;
}

function makeMockFetch(responses: MockResponse[]) {
  let i = 0;
  const calls: Array<{ url: string; method: string; body?: unknown }> = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    let body: unknown;
    if (init?.body) {
      try { body = JSON.parse(String(init.body)); } catch { body = init.body; }
    }
    calls.push({ url, method, body });
    const r = responses[Math.min(i, responses.length - 1)]!;
    i++;
    const status = r.status ?? 200;
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => r.body ?? {},
    } as unknown as Response;
  }) as unknown as typeof fetch;
  return { calls, impl };
}

function baseCfg(overrides: Partial<PluginConfig> = {}): PluginConfig {
  return {
    configPath: null,
    baseUrl: "http://ov.test",
    apiKey: "test-key",
    agentId: "copilot-vscode",
    accountId: "",
    userId: "",
    timeoutMs: 5000,
    autoRecall: true,
    recallLimit: 4,
    scoreThreshold: 0.3,
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
    commitTokenThreshold: 200,
    resumeContextBudget: 32000,
    bypassSession: false,
    bypassSessionPatterns: [],
    writePathAsync: false,
    debug: false,
    debugLogPath: "/tmp/test.log",
    ...overrides,
  };
}

function makeState(opts: {
  cfg?: Partial<PluginConfig>;
  responses?: MockResponse[];
} = {}): { state: ParticipantState; calls: Array<{ url: string; method: string; body?: unknown }> } {
  const { calls, impl } = makeMockFetch(opts.responses ?? [{ body: { result: {} } }]);
  const cfg = baseCfg(opts.cfg);
  const logger = createDebugLogger(cfg);
  const client = new OVClient(cfg, { logger, fetchImpl: impl });
  const cache = new RecallCache();
  const queue = new CommitQueue({
    sessionId: SESSION_ID,
    client,
    threshold: cfg.commitTokenThreshold,
    async: cfg.writePathAsync,
    logger,
  });
  const state: ParticipantState = {
    cfg,
    client,
    cache,
    queue,
    sessionId: SESSION_ID,
    logger,
  };
  return { state, calls };
}

describe("buildRecallContext", () => {
  it("returns null block when query is shorter than minQueryLength", async () => {
    const { state } = makeState();
    const out = await buildRecallContext(state, "hi"); // len 2, floor 3
    expect(out.block).toBeNull();
    expect(out.hits).toBe(0);
  });

  it("returns null block when autoRecall is disabled", async () => {
    const { state } = makeState({ cfg: { autoRecall: false } });
    const out = await buildRecallContext(state, "long-enough-query");
    expect(out.block).toBeNull();
  });

  it("renders a non-empty block when OV returns hits", async () => {
    const hits: RecallHit[] = [
      { uri: "viking://m/1", score: 0.9, type: "memory", abstract: "matched memory" },
    ];
    const { state, calls } = makeState({
      responses: [{ body: { result: { memories: hits } } }],
    });
    const out = await buildRecallContext(state, "auth migration");
    expect(out.block).not.toBeNull();
    expect(out.block).toContain("openviking-context");
    expect(out.block).toContain("matched memory");
    expect(out.hits).toBeGreaterThan(0);
    expect(calls).toHaveLength(1);
  });

  it("returns null block when OV returns no hits", async () => {
    const { state } = makeState({ responses: [{ body: { result: { memories: [] } } }] });
    const out = await buildRecallContext(state, "no matches here");
    expect(out.block).toBeNull();
  });

  it("returns null block when OV returns hits below scoreThreshold", async () => {
    const hits: RecallHit[] = [
      { uri: "viking://m/low", score: 0.1, type: "memory", abstract: "low score" },
    ];
    const { state } = makeState({
      cfg: { scoreThreshold: 0.5 },
      responses: [{ body: { result: { memories: hits } } }],
    });
    const out = await buildRecallContext(state, "low-score query");
    expect(out.block).toBeNull();
  });

  it("caches the recall result so a second call doesn't re-fetch", async () => {
    const hits: RecallHit[] = [
      { uri: "viking://m/1", score: 0.9, type: "memory", abstract: "cached" },
    ];
    const { state, calls } = makeState({
      responses: [{ body: { result: { memories: hits } } }],
    });
    await buildRecallContext(state, "same query");
    await buildRecallContext(state, "same query");
    expect(calls).toHaveLength(1);
  });

  it("survives a transport error and returns an empty result", async () => {
    const { state } = makeState({
      responses: [{ status: 500, body: { error: { message: "down" } } }],
    });
    const out = await buildRecallContext(state, "any query");
    expect(out.block).toBeNull();
  });
});

describe("runStore", () => {
  it("rejects empty input without touching the network", async () => {
    const { state, calls } = makeState();
    const out = await runStore(state, "   ");
    expect(out.ok).toBe(false);
    expect(calls).toHaveLength(0);
  });

  it("appends the message and forces a flush", async () => {
    const { state, calls } = makeState({
      responses: [
        { body: { result: { written: 1 } } }, // appendTurns
        { body: { result: {} } }, // commit (forced)
      ],
    });
    const out = await runStore(state, "remember the auth migration plan");
    expect(out.ok).toBe(true);
    expect(calls.map((c) => c.method)).toEqual(["POST", "POST"]);
    expect(calls[0]!.url).toContain("/messages");
    expect(calls[0]!.body).toMatchObject({ role: "user" });
    expect(calls[1]!.url).toContain("/commit");
    expect(calls[1]!.body).toEqual({ force: true });
  });

  it("reports failure when appendTurns fails", async () => {
    const { state } = makeState({
      responses: [{ status: 500, body: { error: { message: "boom" } } }],
    });
    const out = await runStore(state, "hello");
    expect(out.ok).toBe(false);
    expect(out.message).toMatch(/Failed to append/i);
  });
});

describe("runForget", () => {
  it("rejects empty input with usage message", async () => {
    const { state } = makeState();
    const out = await runForget(state, "");
    expect(out.ok).toBe(false);
    expect(out.message).toMatch(/Usage/);
  });

  it("rejects URIs that don't start with viking://", async () => {
    const { state, calls } = makeState();
    const out = await runForget(state, "https://example.com");
    expect(out.ok).toBe(false);
    expect(calls).toHaveLength(0);
  });

  it("issues DELETE /api/v1/fs?uri=... on a valid viking:// URI", async () => {
    const { state, calls } = makeState({
      responses: [{ body: { result: { uri: "viking://m/1" } } }],
    });
    const out = await runForget(state, "viking://m/1");
    expect(out.ok).toBe(true);
    expect(calls[0]!.method).toBe("DELETE");
    expect(calls[0]!.url).toContain("/api/v1/fs?uri=");
  });

  it("reports failure when the server returns an error", async () => {
    const { state } = makeState({
      responses: [{ status: 404, body: { error: { message: "not found" } } }],
    });
    const out = await runForget(state, "viking://nonexistent");
    expect(out.ok).toBe(false);
    expect(out.message).toContain("not found");
  });
});
