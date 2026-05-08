import { describe, expect, it, vi } from "vitest";
import type { PluginConfig } from "../config.js";
import type {
  OVResult,
  RecallHit,
  RecallOptions,
} from "../ov-client.js";
import { runDebugRecall, type RecallDebuggerClient } from "../debug/recall-debugger.js";

function fakeCfg(overrides: Partial<PluginConfig> = {}): PluginConfig {
  return {
    configPath: null,
    baseUrl: "http://ov.test",
    apiKey: "sk-secret-very-long",
    agentId: "copilot-cli",
    accountId: "team",
    userId: "alice",
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

function makeClient(opts: {
  health?: OVResult<unknown>;
  recall?: OVResult<RecallHit[]>;
} = {}): RecallDebuggerClient & {
  health: ReturnType<typeof vi.fn>;
  recall: ReturnType<typeof vi.fn>;
} {
  return {
    health: vi.fn(async () => opts.health ?? { ok: true, value: { status: "ok" } }),
    recall: vi.fn(async (_q: string, _opts: RecallOptions) =>
      opts.recall ?? ({ ok: true, value: [] } as OVResult<RecallHit[]>),
    ),
  };
}

describe("runDebugRecall — happy path", () => {
  it("emits config snapshot, health, query profile, ranked list, and final block", async () => {
    const cfg = fakeCfg();
    const client = makeClient({
      recall: {
        ok: true,
        value: [
          { uri: "viking://m/high", score: 0.9, type: "memory", abstract: "auth migration plan" },
          { uri: "viking://m/low", score: 0.1, type: "memory", abstract: "below threshold" },
        ],
      },
    });

    const res = await runDebugRecall({ query: "auth migration" }, { cfg, client });

    expect(res.exitCode).toBe(0);
    expect(res.output).toContain("=== OpenViking debug-recall ===");
    expect(res.output).toContain("Configuration");
    expect(res.output).toContain("Health check");
    expect(res.output).toContain("OK");
    expect(res.output).toContain("Query");
    expect(res.output).toContain("tokens        : [auth, migration]");
    expect(res.output).toContain("Recall request");
    expect(res.output).toContain("Ranked");
    expect(res.output).toContain("viking://m/high");
    expect(res.output).not.toContain("viking://m/low"); // dropped by scoreThreshold
    expect(res.output).toContain("Final <openviking-context> block");
    expect(res.output).toContain("Telemetry");
    expect(res.output).toContain("budgetUsed");
  });

  it("redacts apiKey to <set, N chars> — never the literal value", async () => {
    const cfg = fakeCfg({ apiKey: "sk-this-must-not-appear" });
    const res = await runDebugRecall({ query: "x" }, { cfg, client: makeClient() });
    expect(res.output).not.toContain("sk-this-must-not-appear");
    expect(res.output).toMatch(/<set, \d+ chars>/);
  });

  it("streams output through the optional `write` sink", async () => {
    const cfg = fakeCfg();
    const chunks: string[] = [];
    await runDebugRecall(
      { query: "stream test" },
      { cfg, client: makeClient(), write: (c) => chunks.push(c) },
    );
    expect(chunks.length).toBeGreaterThan(5);
    expect(chunks.join("")).toContain("debug-recall");
  });
});

describe("runDebugRecall — failure paths", () => {
  it("reports an unhealthy server but continues to attempt recall", async () => {
    const cfg = fakeCfg();
    const client = makeClient({ health: { ok: false, error: { message: "down", status: 503 } } });
    const res = await runDebugRecall({ query: "after-health-failure" }, { cfg, client });
    expect(res.exitCode).toBe(1);
    expect(res.output).toContain("Health check");
    expect(res.output).toContain("ERROR: down");
    expect(res.output).toContain("HTTP 503");
    expect(res.output).toContain("continuing");
    // Recall was still attempted
    expect(client.recall).toHaveBeenCalledTimes(1);
  });

  it("aborts the report when recall itself fails", async () => {
    const cfg = fakeCfg();
    const client = makeClient({ recall: { ok: false, error: { message: "invalid query" } } });
    const res = await runDebugRecall({ query: "broken" }, { cfg, client });
    expect(res.exitCode).toBe(2);
    expect(res.output).toContain("Recall request");
    expect(res.output).toContain("ERROR: invalid query");
    expect(res.output).toContain("Cannot continue");
    expect(res.output).not.toContain("Final <openviking-context>");
  });

  it("notes when the ranked list is empty under the configured threshold", async () => {
    const cfg = fakeCfg({ scoreThreshold: 0.95 });
    const client = makeClient({
      recall: {
        ok: true,
        value: [{ uri: "viking://m/x", score: 0.5, type: "memory", abstract: "below" }],
      },
    });
    const res = await runDebugRecall({ query: "filtered out" }, { cfg, client });
    expect(res.exitCode).toBe(0);
    expect(res.output).toContain("(no hits at or above scoreThreshold 0.95)");
  });
});
