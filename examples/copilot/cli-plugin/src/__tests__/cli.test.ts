import { describe, expect, it, vi } from "vitest";
import type { LoadConfigOptions, PluginConfig } from "@openviking/copilot-shared";
import { runMain } from "../cli.js";

interface CapturedStreams {
  stdout: string;
  stderr: string;
}

function makeStreams(): CapturedStreams & {
  stdoutFn: (c: string) => void;
  stderrFn: (c: string) => void;
} {
  const obj: CapturedStreams & {
    stdoutFn?: (c: string) => void;
    stderrFn?: (c: string) => void;
  } = { stdout: "", stderr: "" };
  obj.stdoutFn = (c) => { obj.stdout += c; };
  obj.stderrFn = (c) => { obj.stderr += c; };
  return obj as CapturedStreams & {
    stdoutFn: (c: string) => void;
    stderrFn: (c: string) => void;
  };
}

function fakeCfg(overrides: Partial<PluginConfig> = {}): PluginConfig {
  return {
    configPath: null,
    baseUrl: "http://127.0.0.1:1933",
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

describe("runMain — --help", () => {
  it("prints usage to stdout and exits 0", async () => {
    const s = makeStreams();
    const code = await runMain(["--help"], { stdout: s.stdoutFn, stderr: s.stderrFn });
    expect(code).toBe(0);
    expect(s.stdout).toContain("openviking-copilot-mcp [options]");
    expect(s.stdout).toContain("--help");
    expect(s.stdout).toContain("--version");
    expect(s.stdout).toContain("--check");
    expect(s.stderr).toBe("");
  });

  it("treats -h as a synonym for --help", async () => {
    const s = makeStreams();
    const code = await runMain(["-h"], { stdout: s.stdoutFn, stderr: s.stderrFn });
    expect(code).toBe(0);
    expect(s.stdout).toContain("openviking-copilot-mcp [options]");
  });
});

describe("runMain — --version", () => {
  it("prints a version string to stdout and exits 0", async () => {
    const s = makeStreams();
    const code = await runMain(["--version"], { stdout: s.stdoutFn, stderr: s.stderrFn });
    expect(code).toBe(0);
    // In tests __OV_CLI_VERSION__ isn't injected; the fallback "0.0.0" is what we ship.
    expect(s.stdout.trim()).toMatch(/^\d+\.\d+\.\d+/);
  });

  it("treats -v as a synonym for --version", async () => {
    const s = makeStreams();
    const code = await runMain(["-v"], { stdout: s.stdoutFn, stderr: s.stderrFn });
    expect(code).toBe(0);
    expect(s.stdout.trim()).toMatch(/^\d+\.\d+\.\d+/);
  });
});

describe("runMain — --check", () => {
  it("invokes loadConfig with agentIdDefault: 'copilot-cli'", async () => {
    const s = makeStreams();
    const loadConfig = vi.fn((_opts: LoadConfigOptions) => fakeCfg());
    const isPluginEnabled = vi.fn(() => true);
    await runMain(["--check"], {
      stdout: s.stdoutFn, stderr: s.stderrFn,
      loadConfig, isPluginEnabled,
    });
    expect(loadConfig).toHaveBeenCalledTimes(1);
    expect(loadConfig.mock.calls[0]![0]).toMatchObject({ agentIdDefault: "copilot-cli" });
  });

  it("prints a redacted summary including baseUrl + agentId", async () => {
    const s = makeStreams();
    await runMain(["--check"], {
      stdout: s.stdoutFn, stderr: s.stderrFn,
      loadConfig: () => fakeCfg({ baseUrl: "https://ov.test", agentId: "copilot-cli" }),
      isPluginEnabled: () => true,
    });
    expect(s.stdout).toContain("baseUrl");
    expect(s.stdout).toContain("https://ov.test");
    expect(s.stdout).toContain("agentId");
    expect(s.stdout).toContain("copilot-cli");
  });

  it("redacts apiKey to a length marker, never the value", async () => {
    const s = makeStreams();
    await runMain(["--check"], {
      stdout: s.stdoutFn, stderr: s.stderrFn,
      loadConfig: () => fakeCfg({ apiKey: "sk-secret-very-long-key-here" }),
      isPluginEnabled: () => true,
    });
    expect(s.stdout).not.toContain("sk-secret-very-long-key-here");
    expect(s.stdout).toMatch(/<set, \d+ chars>/);
  });

  it("returns exit code 0 when enabled, non-zero when disabled", async () => {
    const s1 = makeStreams();
    const c1 = await runMain(["--check"], {
      stdout: s1.stdoutFn, stderr: s1.stderrFn,
      loadConfig: () => fakeCfg(),
      isPluginEnabled: () => true,
    });
    expect(c1).toBe(0);

    const s2 = makeStreams();
    const c2 = await runMain(["--check"], {
      stdout: s2.stdoutFn, stderr: s2.stderrFn,
      loadConfig: () => fakeCfg(),
      isPluginEnabled: () => false,
    });
    expect(c2).not.toBe(0);
  });
});

describe("runMain — default invocation (no flags)", () => {
  it("prints the issue-#21 stub message to stderr and exits 0", async () => {
    const s = makeStreams();
    const code = await runMain([], { stdout: s.stdoutFn, stderr: s.stderrFn });
    expect(code).toBe(0);
    expect(s.stderr).toContain("scaffold");
    expect(s.stderr).toContain("#21");
    expect(s.stdout).toBe("");
  });
});

describe("runMain — argv error handling", () => {
  it("rejects unknown positional arguments with exit code 2", async () => {
    const s = makeStreams();
    const code = await runMain(["surprise"], { stdout: s.stdoutFn, stderr: s.stderrFn });
    expect(code).toBe(2);
    expect(s.stderr).toContain("Unknown positional argument");
    expect(s.stderr).toContain("--help");
  });
});
