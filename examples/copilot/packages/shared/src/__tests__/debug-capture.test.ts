import { describe, expect, it } from "vitest";
import type { PluginConfig } from "../config.js";
import { runDebugCapture } from "../debug/capture-debugger.js";

function fakeCfg(overrides: Partial<PluginConfig> = {}): PluginConfig {
  return {
    configPath: null,
    baseUrl: "http://ov.test",
    apiKey: "",
    agentId: "copilot-cli",
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
    commitTokenThreshold: 200,
    resumeContextBudget: 32000,
    bypassSession: false,
    bypassSessionPatterns: [],
    writePathAsync: true,
    debug: false,
    debugLogPath: "/tmp/test.log",
    ...overrides,
  };
}

const HAPPY_TRANSCRIPT = JSON.stringify([
  { role: "user", text: "<openviking-context>recall</openviking-context>real user question" },
  { role: "assistant", text: "real assistant reply <system-reminder>internal</system-reminder>" },
]);

describe("runDebugCapture — happy path", () => {
  it("prints config + per-turn KEEP/DROP + final payload + token projection", async () => {
    const cfg = fakeCfg();
    const res = await runDebugCapture(
      { path: "/fake/transcript.json" },
      { cfg, readFile: () => HAPPY_TRANSCRIPT },
    );
    expect(res.exitCode).toBe(0);
    expect(res.output).toContain("=== OpenViking debug-capture ===");
    expect(res.output).toContain("Configuration");
    expect(res.output).toContain("captureMaxLength");
    expect(res.output).toContain("commitTokenThreshold");
    expect(res.output).toContain("Per-turn analysis");
    expect(res.output).toMatch(/\[ 0\] user.*KEEP/);
    expect(res.output).toMatch(/\[ 1\] assistant.*KEEP/);
    expect(res.output).toContain("Final OVTurn[] payload");
    expect(res.output).toContain("preview: real user question");
    expect(res.output).toContain("preview: real assistant reply");
    expect(res.output).toContain("Commit-queue projection");
    expect(res.output).toContain("would trigger commit");
  });

  it("flags YES when the projected token total crosses commitTokenThreshold", async () => {
    const cfg = fakeCfg({ commitTokenThreshold: 1 });
    const res = await runDebugCapture(
      { path: "/x" },
      { cfg, readFile: () => HAPPY_TRANSCRIPT },
    );
    expect(res.output).toContain("would trigger commit  : YES");
  });

  it("flags `no` when the projected total stays below the threshold", async () => {
    const cfg = fakeCfg({ commitTokenThreshold: 100_000 });
    const res = await runDebugCapture(
      { path: "/x" },
      { cfg, readFile: () => HAPPY_TRANSCRIPT },
    );
    expect(res.output).toContain("would trigger commit  : no");
  });
});

describe("runDebugCapture — drop reasons", () => {
  it("marks the user turn as DROP (empty-after-sanitise) when only injected blocks remain", async () => {
    const cfg = fakeCfg();
    const transcript = JSON.stringify([
      { role: "user", text: "<openviking-context>only this</openviking-context>" },
      { role: "assistant", text: "kept" },
    ]);
    const res = await runDebugCapture({ path: "/x" }, { cfg, readFile: () => transcript });
    expect(res.output).toContain("DROP (empty-after-sanitise)");
  });

  it("marks assistant turns as DROP (filtered-assistant) when captureAssistantTurns=false", async () => {
    const cfg = fakeCfg({ captureAssistantTurns: false });
    const res = await runDebugCapture(
      { path: "/x" },
      { cfg, readFile: () => HAPPY_TRANSCRIPT },
    );
    expect(res.output).toContain("DROP (filtered-assistant)");
  });

  it("marks turns as DROP (too-long) when sanitised length exceeds captureMaxLength", async () => {
    const cfg = fakeCfg({ captureMaxLength: 10 });
    const transcript = JSON.stringify([
      { role: "user", text: "x".repeat(50) },
    ]);
    const res = await runDebugCapture({ path: "/x" }, { cfg, readFile: () => transcript });
    expect(res.output).toContain("DROP (too-long)");
  });

  it("notes bad-shape entries without crashing", async () => {
    const cfg = fakeCfg();
    const transcript = JSON.stringify([
      { role: "user", text: "good" },
      { role: "system", text: "bad-role" },
      { role: "assistant" /* missing text */ },
      "string-not-object",
    ]);
    const res = await runDebugCapture({ path: "/x" }, { cfg, readFile: () => transcript });
    expect(res.output).toContain("bad-shape entries     : 3");
  });
});

describe("runDebugCapture — failure paths", () => {
  it("returns exit 2 when readFile throws (file not found)", async () => {
    const cfg = fakeCfg();
    const res = await runDebugCapture(
      { path: "/missing.json" },
      {
        cfg,
        readFile: () => { throw new Error("ENOENT: no such file or directory"); },
      },
    );
    expect(res.exitCode).toBe(2);
    expect(res.output).toContain("ENOENT");
  });

  it("returns exit 2 on invalid JSON", async () => {
    const cfg = fakeCfg();
    const res = await runDebugCapture(
      { path: "/x" },
      { cfg, readFile: () => "{not-json" },
    );
    expect(res.exitCode).toBe(2);
    expect(res.output).toContain("not valid JSON");
  });

  it("returns exit 2 when the JSON top level isn't an array", async () => {
    const cfg = fakeCfg();
    const res = await runDebugCapture(
      { path: "/x" },
      { cfg, readFile: () => JSON.stringify({ user: "wrong-shape" }) },
    );
    expect(res.exitCode).toBe(2);
    expect(res.output).toContain("must be an array");
  });
});

describe("runDebugCapture — bypass + autoCapture notes", () => {
  it("appends a bypass note when cfg.bypassSession is true", async () => {
    const cfg = fakeCfg({ bypassSession: true });
    const res = await runDebugCapture(
      { path: "/x" },
      { cfg, readFile: () => HAPPY_TRANSCRIPT },
    );
    expect(res.output).toContain("Bypass note");
    expect(res.output).toContain("short-circuit");
  });

  it("appends an autoCapture note when cfg.autoCapture is false", async () => {
    const cfg = fakeCfg({ autoCapture: false });
    const res = await runDebugCapture(
      { path: "/x" },
      { cfg, readFile: () => HAPPY_TRANSCRIPT },
    );
    expect(res.output).toContain("autoCapture note");
    expect(res.output).toContain("short-circuit");
  });
});
