import { describe, expect, it, vi } from "vitest";
import {
  CommitQueue,
  OVClient,
  type PluginConfig,
  createDebugLogger,
} from "@openviking/copilot-shared";
import { captureChatTurn } from "../capture/on-response";

const SESSION_ID = "cp-capture-test";

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
    apiKey: "key",
    agentId: "copilot-vscode",
    accountId: "",
    userId: "",
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
    commitTokenThreshold: 1_000_000, // very high so threshold-triggered commits don't fire in these tests
    resumeContextBudget: 32000,
    bypassSession: false,
    bypassSessionPatterns: [],
    writePathAsync: false,
    debug: false,
    debugLogPath: "/tmp/test.log",
    ...overrides,
  };
}

function buildHarness(overrides: Partial<PluginConfig> = {}, responses?: MockResponse[]) {
  const cfg = baseCfg(overrides);
  const { calls, impl } = makeMockFetch(
    responses ?? [
      { body: { result: { written: 1 } } }, // appendTurn 1
      { body: { result: { written: 1 } } }, // appendTurn 2
      { body: { result: {} } }, // commit (only fires if threshold crossed)
    ],
  );
  const logger = createDebugLogger(cfg);
  const client = new OVClient(cfg, { logger, fetchImpl: impl });
  const queue = new CommitQueue({
    sessionId: SESSION_ID,
    client,
    threshold: cfg.commitTokenThreshold,
    async: cfg.writePathAsync,
    logger,
  });
  return { cfg, queue, logger, calls };
}

describe("captureChatTurn — gating", () => {
  it("skips when autoCapture=false (no enqueue, no network)", async () => {
    const { cfg, queue, logger, calls } = buildHarness({ autoCapture: false });
    const res = await captureChatTurn({
      cfg, queue, logger,
      userText: "hello",
      assistantText: "hi back",
    });
    expect(res.skipped).toBe(true);
    expect(res.enqueued).toBe(0);
    expect(calls).toHaveLength(0);
  });

  it("skips when both turns are empty after canonicalisation", async () => {
    const { cfg, queue, logger, calls } = buildHarness();
    const res = await captureChatTurn({
      cfg, queue, logger,
      userText: "<openviking-context>only injected</openviking-context>",
      assistantText: "   \n\n  ",
    });
    expect(res.skipped).toBe(true);
    expect(res.enqueued).toBe(0);
    expect(calls).toHaveLength(0);
  });
});

describe("captureChatTurn — happy paths", () => {
  it("enqueues both user and assistant turns when captureAssistantTurns=true", async () => {
    const { cfg, queue, logger, calls } = buildHarness();
    const res = await captureChatTurn({
      cfg, queue, logger,
      userText: "what's the auth migration plan?",
      assistantText: "here are the four steps...",
    });
    expect(res.skipped).toBe(false);
    expect(res.enqueued).toBe(2);
    expect(calls).toHaveLength(2);
    const bodies = calls.map((c) => c.body) as Array<{ role: string }>;
    expect(bodies[0]!.role).toBe("user");
    expect(bodies[1]!.role).toBe("assistant");
  });

  it("user-only mode (captureAssistantTurns=false) drops the assistant turn", async () => {
    const { cfg, queue, logger, calls } = buildHarness(
      { captureAssistantTurns: false },
      [{ body: { result: { written: 1 } } }],
    );
    const res = await captureChatTurn({
      cfg, queue, logger,
      userText: "store this",
      assistantText: "ok",
    });
    expect(res.enqueued).toBe(1);
    expect(calls).toHaveLength(1);
    expect((calls[0]!.body as { role: string }).role).toBe("user");
  });

  it("strips injected blocks from BOTH user and assistant text before storing", async () => {
    const { cfg, queue, logger, calls } = buildHarness();
    await captureChatTurn({
      cfg, queue, logger,
      userText: "<openviking-context>recalled</openviking-context>\nReal user message",
      assistantText: "Real assistant reply <system-reminder>internal</system-reminder>",
    });
    const userBody = calls[0]!.body as { role: string; content: string };
    const assistantBody = calls[1]!.body as { role: string; content: string };
    expect(userBody.content).not.toMatch(/openviking-context|recalled/);
    expect(userBody.content).toContain("Real user message");
    expect(assistantBody.content).not.toMatch(/system-reminder|internal/);
    expect(assistantBody.content).toContain("Real assistant reply");
  });
});

describe("captureChatTurn — bypass transparency", () => {
  it("bypassSession=true returns ok without any HTTP calls", async () => {
    const { cfg, queue, logger, calls } = buildHarness({ bypassSession: true });
    const res = await captureChatTurn({
      cfg, queue, logger,
      userText: "real user text",
      assistantText: "real assistant text",
    });
    // The OVClient bypass returns ok=true with skipped:N, so the queue
    // thinks the append succeeded and counts the appended turns. Net
    // result is the host's hot path stays clean — no errors, no
    // network round-trips.
    expect(res.skipped).toBe(false);
    expect(res.enqueued).toBe(2);
    expect(calls).toHaveLength(0); // no actual HTTP calls
  });

  it("bypassSessionPatterns matching the cwd also short-circuits the network", async () => {
    const cfg = baseCfg({ bypassSessionPatterns: ["/scratch/**"] });
    const { calls, impl } = makeMockFetch([{ body: {} }]);
    const logger = createDebugLogger(cfg);
    // Build the OVClient with a matching cwd so the bypass kicks in.
    const client = new OVClient(cfg, {
      logger,
      fetchImpl: impl,
      bypassContext: { cwd: "/scratch/throwaway-xyz" },
    });
    const queue = new CommitQueue({
      sessionId: SESSION_ID,
      client,
      threshold: cfg.commitTokenThreshold,
      async: cfg.writePathAsync,
      logger,
    });

    const res = await captureChatTurn({
      cfg, queue, logger,
      userText: "in scratch",
      assistantText: "still in scratch",
    });

    expect(res.skipped).toBe(false);
    expect(calls).toHaveLength(0);
  });
});

describe("captureChatTurn — async-detached path", () => {
  it("when writePathAsync=true and threshold is crossed, the commit dispatches detached (no inline await)", async () => {
    const cfg = baseCfg({
      writePathAsync: true,
      commitTokenThreshold: 1, // tiny so crossing is guaranteed
    });
    let commitDelayResolve!: () => void;
    const { calls, impl } = makeMockFetch([
      { body: { result: { written: 1 } } }, // append #1
      { body: { result: { written: 1 } } }, // append #2
    ]);
    const logger = createDebugLogger(cfg);
    const client = new OVClient(cfg, { logger, fetchImpl: impl });
    // Wrap the client's commit in something that would block forever
    // unless dispatched detached; runWriteTask returning detached:true
    // means the queue's syncHandler never runs.
    const wrappedClient = {
      appendTurns: client.appendTurns.bind(client),
      commit: vi.fn(async () => {
        await new Promise<void>((r) => {
          commitDelayResolve = r;
        });
        return { ok: true as const, value: {} };
      }),
    };
    const queue = new CommitQueue({
      sessionId: SESSION_ID,
      client: wrappedClient,
      threshold: cfg.commitTokenThreshold,
      async: true,
      asyncSpawn: () => ({ command: "/usr/bin/true", args: [] }),
      logger,
    });

    const start = Date.now();
    const res = await captureChatTurn({
      cfg, queue, logger,
      userText: "any",
      assistantText: "any",
    });
    const elapsed = Date.now() - start;

    expect(res.enqueued).toBe(2);
    expect(elapsed).toBeLessThan(150);
    // The mock commit was NOT awaited — the detached spawn ran
    // /usr/bin/true instead.
    expect(wrappedClient.commit).not.toHaveBeenCalled();
    expect(calls).toHaveLength(2); // just the two appendTurns
    commitDelayResolve?.(); // unblock any stale promise so the test runner exits
  });
});
