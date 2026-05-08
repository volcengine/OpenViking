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
  it("starts the stdio MCP server and exits 0", async () => {
    const s = makeStreams();
    const loadConfig = vi.fn((_opts: LoadConfigOptions) => fakeCfg());
    const runStdioMcpServer = vi.fn(async () => undefined);
    const code = await runMain([], {
      stdout: s.stdoutFn,
      stderr: s.stderrFn,
      loadConfig,
      runStdioMcpServer,
    });
    expect(code).toBe(0);
    expect(runStdioMcpServer).toHaveBeenCalledWith({ version: "0.0.0", loadConfig });
    expect(s.stderr).toBe("");
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

describe("runMain — --commit-flush", () => {
  it("rejects when --session=<id> is missing with exit code 2", async () => {
    const s = makeStreams();
    const loadConfig = vi.fn((_opts: LoadConfigOptions) => fakeCfg());
    const commitFlush = vi.fn();
    const code = await runMain(["--commit-flush"], {
      stdout: s.stdoutFn, stderr: s.stderrFn,
      loadConfig, commitFlush,
    });
    expect(code).toBe(2);
    expect(s.stderr).toContain("requires --session");
    expect(commitFlush).not.toHaveBeenCalled();
  });

  it("calls commitFlush(cfg, sessionId) and exits 0 on ok", async () => {
    const s = makeStreams();
    const loadConfig = vi.fn((_opts: LoadConfigOptions) => fakeCfg({ apiKey: "test" }));
    const commitFlush = vi.fn(async () => ({ ok: true as const, value: { committed: true } }));
    const code = await runMain(["--commit-flush", "--session=cp-abc"], {
      stdout: s.stdoutFn, stderr: s.stderrFn,
      loadConfig, commitFlush,
    });
    expect(code).toBe(0);
    expect(commitFlush).toHaveBeenCalledTimes(1);
    expect(commitFlush.mock.calls[0]![0]!.apiKey).toBe("test");
    expect(commitFlush.mock.calls[0]![1]).toBe("cp-abc");
    expect(s.stdout).toBe("");
    expect(s.stderr).toBe("");
  });

  it("exits 1 with a stderr message when commitFlush returns ok:false", async () => {
    const s = makeStreams();
    const loadConfig = vi.fn((_opts: LoadConfigOptions) => fakeCfg());
    const commitFlush = vi.fn(async () => ({
      ok: false as const,
      error: { message: "ECONNREFUSED", status: 0 },
    }));
    const code = await runMain(["--commit-flush", "--session=cp-z"], {
      stdout: s.stdoutFn, stderr: s.stderrFn,
      loadConfig, commitFlush,
    });
    expect(code).toBe(1);
    expect(s.stderr).toContain("commit-flush failed");
    expect(s.stderr).toContain("ECONNREFUSED");
  });

  it("includes HTTP status when present in the error", async () => {
    const s = makeStreams();
    const loadConfig = vi.fn((_opts: LoadConfigOptions) => fakeCfg());
    const commitFlush = vi.fn(async () => ({
      ok: false as const,
      error: { message: "not found", status: 404 },
    }));
    const code = await runMain(["--commit-flush", "--session=cp-z"], {
      stdout: s.stdoutFn, stderr: s.stderrFn,
      loadConfig, commitFlush,
    });
    expect(code).toBe(1);
    expect(s.stderr).toContain("HTTP 404");
    expect(s.stderr).toContain("not found");
  });

  it("trims whitespace in the --session value before passing it through", async () => {
    const s = makeStreams();
    const loadConfig = vi.fn((_opts: LoadConfigOptions) => fakeCfg());
    const commitFlush = vi.fn(async () => ({ ok: true as const, value: {} }));
    const code = await runMain(["--commit-flush", "--session=  cp-spaced  "], {
      stdout: s.stdoutFn, stderr: s.stderrFn,
      loadConfig, commitFlush,
    });
    expect(code).toBe(0);
    expect(commitFlush.mock.calls[0]![1]).toBe("cp-spaced");
  });

  it("rejects an empty --session= value (whitespace-only) with exit code 2", async () => {
    const s = makeStreams();
    const loadConfig = vi.fn((_opts: LoadConfigOptions) => fakeCfg());
    const commitFlush = vi.fn();
    const code = await runMain(["--commit-flush", "--session=   "], {
      stdout: s.stdoutFn, stderr: s.stderrFn,
      loadConfig, commitFlush,
    });
    expect(code).toBe(2);
    expect(commitFlush).not.toHaveBeenCalled();
  });
});

describe("runMain — --debug-recall", () => {
  it("rejects when no query is supplied with exit 2", async () => {
    const s = makeStreams();
    const debugRecallRunner = vi.fn();
    const code = await runMain(["--debug-recall"], {
      stdout: s.stdoutFn, stderr: s.stderrFn,
      loadConfig: vi.fn(() => fakeCfg()),
      debugRecallRunner,
    });
    expect(code).toBe(2);
    expect(s.stderr).toContain("requires a query");
    expect(debugRecallRunner).not.toHaveBeenCalled();
  });

  it("rejects --debug-recall= (empty value) with exit 2", async () => {
    const s = makeStreams();
    const debugRecallRunner = vi.fn();
    const code = await runMain(["--debug-recall="], {
      stdout: s.stdoutFn, stderr: s.stderrFn,
      loadConfig: vi.fn(() => fakeCfg()),
      debugRecallRunner,
    });
    expect(code).toBe(2);
    expect(debugRecallRunner).not.toHaveBeenCalled();
  });

  it("invokes the runner with cfg + trimmed query and writes its output", async () => {
    const s = makeStreams();
    const debugRecallRunner = vi.fn(async () => ({ exitCode: 0, output: "<<recall report>>" }));
    const loadConfig = vi.fn((_opts: LoadConfigOptions) => fakeCfg({ apiKey: "k" }));
    const code = await runMain(["--debug-recall=  auth migration  "], {
      stdout: s.stdoutFn, stderr: s.stderrFn,
      loadConfig, debugRecallRunner,
    });
    expect(code).toBe(0);
    expect(debugRecallRunner).toHaveBeenCalledTimes(1);
    expect(debugRecallRunner.mock.calls[0]![0]!.apiKey).toBe("k");
    expect(debugRecallRunner.mock.calls[0]![1]).toBe("auth migration");
    expect(s.stdout).toBe("<<recall report>>");
  });

  it("propagates the runner's exit code on failure", async () => {
    const s = makeStreams();
    const debugRecallRunner = vi.fn(async () => ({ exitCode: 2, output: "Cannot continue" }));
    const code = await runMain(["--debug-recall=q"], {
      stdout: s.stdoutFn, stderr: s.stderrFn,
      loadConfig: vi.fn(() => fakeCfg()),
      debugRecallRunner,
    });
    expect(code).toBe(2);
    expect(s.stdout).toContain("Cannot continue");
  });
});

describe("runMain — --debug-capture", () => {
  it("rejects when no path is supplied with exit 2", async () => {
    const s = makeStreams();
    const debugCaptureRunner = vi.fn();
    const code = await runMain(["--debug-capture"], {
      stdout: s.stdoutFn, stderr: s.stderrFn,
      loadConfig: vi.fn(() => fakeCfg()),
      debugCaptureRunner,
    });
    expect(code).toBe(2);
    expect(s.stderr).toContain("requires a transcript file path");
    expect(debugCaptureRunner).not.toHaveBeenCalled();
  });

  it("invokes the runner with cfg + trimmed path", async () => {
    const s = makeStreams();
    const debugCaptureRunner = vi.fn(async () => ({ exitCode: 0, output: "<<capture report>>" }));
    const loadConfig = vi.fn((_opts: LoadConfigOptions) => fakeCfg());
    const code = await runMain(["--debug-capture=  ./transcript.json  "], {
      stdout: s.stdoutFn, stderr: s.stderrFn,
      loadConfig, debugCaptureRunner,
    });
    expect(code).toBe(0);
    expect(debugCaptureRunner.mock.calls[0]![1]).toBe("./transcript.json");
    expect(s.stdout).toBe("<<capture report>>");
  });

  it("propagates the runner's exit code on failure", async () => {
    const s = makeStreams();
    const debugCaptureRunner = vi.fn(async () => ({ exitCode: 2, output: "ENOENT" }));
    const code = await runMain(["--debug-capture=/nope"], {
      stdout: s.stdoutFn, stderr: s.stderrFn,
      loadConfig: vi.fn(() => fakeCfg()),
      debugCaptureRunner,
    });
    expect(code).toBe(2);
    expect(s.stdout).toContain("ENOENT");
  });
});
