import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { isPluginEnabled, loadConfig } from "../config.js";

const ENV_KEYS = [
  "OPENVIKING_MEMORY_ENABLED",
  "OPENVIKING_URL",
  "OPENVIKING_BASE_URL",
  "OPENVIKING_API_KEY",
  "OPENVIKING_BEARER_TOKEN",
  "OPENVIKING_ACCOUNT",
  "OPENVIKING_USER",
  "OPENVIKING_AGENT_ID",
  "OPENVIKING_AUTO_RECALL",
  "OPENVIKING_RECALL_LIMIT",
  "OPENVIKING_RECALL_TOKEN_BUDGET",
  "OPENVIKING_RECALL_MAX_CONTENT_CHARS",
  "OPENVIKING_RECALL_PREFER_ABSTRACT",
  "OPENVIKING_SCORE_THRESHOLD",
  "OPENVIKING_MIN_QUERY_LENGTH",
  "OPENVIKING_LOG_RANKING_DETAILS",
  "OPENVIKING_AUTO_CAPTURE",
  "OPENVIKING_CAPTURE_MODE",
  "OPENVIKING_CAPTURE_MAX_LENGTH",
  "OPENVIKING_CAPTURE_ASSISTANT_TURNS",
  "OPENVIKING_COMMIT_TOKEN_THRESHOLD",
  "OPENVIKING_RESUME_CONTEXT_BUDGET",
  "OPENVIKING_TIMEOUT_MS",
  "OPENVIKING_CAPTURE_TIMEOUT_MS",
  "OPENVIKING_WRITE_PATH_ASYNC",
  "OPENVIKING_BYPASS_SESSION",
  "OPENVIKING_BYPASS_SESSION_PATTERNS",
  "OPENVIKING_DEBUG",
  "OPENVIKING_DEBUG_LOG",
  "OPENVIKING_CONFIG_FILE",
  "OPENVIKING_CLI_CONFIG_FILE",
] as const;

let savedEnv: Record<string, string | undefined> = {};
let tmpDir: string;
let ovConfPath: string;
let cliConfPath: string;

function clearEnv(): void {
  for (const k of ENV_KEYS) delete process.env[k];
}

function pointToTempConfigs(): void {
  process.env["OPENVIKING_CONFIG_FILE"] = ovConfPath;
  process.env["OPENVIKING_CLI_CONFIG_FILE"] = cliConfPath;
}

function writeOv(content: object): void {
  writeFileSync(ovConfPath, JSON.stringify(content));
}

function writeCli(content: object): void {
  writeFileSync(cliConfPath, JSON.stringify(content));
}

beforeEach(() => {
  savedEnv = {};
  for (const k of ENV_KEYS) savedEnv[k] = process.env[k];
  clearEnv();

  tmpDir = mkdtempSync(join(tmpdir(), "ov-cfg-"));
  ovConfPath = join(tmpDir, "ov.conf");
  cliConfPath = join(tmpDir, "ovcli.conf");
  // Point loader at temp paths that *don't exist* by default; tests that need
  // them write the files first.
  pointToTempConfigs();
});

afterEach(() => {
  clearEnv();
  for (const [k, v] of Object.entries(savedEnv)) {
    if (v !== undefined) process.env[k] = v;
  }
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("loadConfig — defaults", () => {
  it("returns built-in defaults when no env / no config file", () => {
    const cfg = loadConfig({ agentIdDefault: "copilot-cli" });
    expect(cfg.baseUrl).toBe("http://127.0.0.1:1933");
    expect(cfg.apiKey).toBe("");
    expect(cfg.agentId).toBe("copilot-cli");
    expect(cfg.recallLimit).toBe(6);
    expect(cfg.scoreThreshold).toBeCloseTo(0.35);
    expect(cfg.captureMode).toBe("semantic");
    expect(cfg.bypassSessionPatterns).toEqual([]);
    expect(cfg.writePathAsync).toBe(true);
    expect(cfg.configPath).toBeNull();
  });

  it("uses agentIdDefault per target", () => {
    const cli = loadConfig({ agentIdDefault: "copilot-cli" });
    const ext = loadConfig({ agentIdDefault: "copilot-vscode" });
    expect(cli.agentId).toBe("copilot-cli");
    expect(ext.agentId).toBe("copilot-vscode");
  });
});

describe("loadConfig — precedence chain", () => {
  it("ovcli.conf overrides ov.conf for connection fields", () => {
    writeOv({ server: { url: "http://from-ov.example", root_api_key: "ov-key" } });
    writeCli({ url: "http://from-cli.example", api_key: "cli-key", account: "tenant", user: "alice" });

    const cfg = loadConfig({ agentIdDefault: "copilot-cli" });
    expect(cfg.baseUrl).toBe("http://from-cli.example");
    expect(cfg.apiKey).toBe("cli-key");
    expect(cfg.accountId).toBe("tenant");
    expect(cfg.userId).toBe("alice");
  });

  it("env vars override ovcli.conf and ov.conf", () => {
    writeOv({ server: { url: "http://from-ov.example" } });
    writeCli({ url: "http://from-cli.example", api_key: "cli-key" });
    process.env["OPENVIKING_URL"] = "http://from-env.example/";
    process.env["OPENVIKING_API_KEY"] = "env-key";
    process.env["OPENVIKING_ACCOUNT"] = "env-tenant";

    const cfg = loadConfig({ agentIdDefault: "copilot-cli" });
    expect(cfg.baseUrl).toBe("http://from-env.example"); // trailing slash trimmed
    expect(cfg.apiKey).toBe("env-key");
    expect(cfg.accountId).toBe("env-tenant");
  });

  it("hostOverrides win over env, ovcli.conf, and ov.conf", () => {
    writeOv({ server: { url: "http://from-ov.example" }, copilot: { recallLimit: 9 } });
    writeCli({ url: "http://from-cli.example", api_key: "cli-key" });
    process.env["OPENVIKING_URL"] = "http://from-env.example";
    process.env["OPENVIKING_RECALL_LIMIT"] = "12";

    const cfg = loadConfig({
      agentIdDefault: "copilot-vscode",
      hostOverrides: {
        baseUrl: "http://from-host.example",
        apiKey: "host-key",
        recallLimit: 4,
      },
    });
    expect(cfg.baseUrl).toBe("http://from-host.example");
    expect(cfg.apiKey).toBe("host-key");
    expect(cfg.recallLimit).toBe(4);
  });

  it("undefined hostOverrides fields fall through to lower layers", () => {
    process.env["OPENVIKING_URL"] = "http://from-env.example";
    const cfg = loadConfig({
      agentIdDefault: "copilot-vscode",
      hostOverrides: { apiKey: "host-key" },
    });
    expect(cfg.baseUrl).toBe("http://from-env.example");
    expect(cfg.apiKey).toBe("host-key");
  });

  it("OPENVIKING_BEARER_TOKEN is accepted as a synonym for OPENVIKING_API_KEY", () => {
    process.env["OPENVIKING_BEARER_TOKEN"] = "bearer-only";
    const cfg = loadConfig({ agentIdDefault: "copilot-cli" });
    expect(cfg.apiKey).toBe("bearer-only");
  });

  it("falls back to {host}:{port} when only server.host/port given", () => {
    writeOv({ server: { host: "0.0.0.0", port: 1944 } });
    const cfg = loadConfig({ agentIdDefault: "copilot-cli" });
    // 0.0.0.0 is rewritten to 127.0.0.1 for client-side use
    expect(cfg.baseUrl).toBe("http://127.0.0.1:1944");
  });
});

describe("loadConfig — copilot vs claude_code blocks", () => {
  it("reads tuning fields from the new `copilot` block", () => {
    writeOv({
      server: { url: "http://example" },
      copilot: { recallLimit: 11, captureMode: "keyword" },
    });
    const cfg = loadConfig({ agentIdDefault: "copilot-cli" });
    expect(cfg.recallLimit).toBe(11);
    expect(cfg.captureMode).toBe("keyword");
  });

  it("falls back to legacy `claude_code` block when `copilot` is absent", () => {
    writeOv({
      server: { url: "http://example" },
      claude_code: { recallLimit: 7, debug: true },
    });
    const cfg = loadConfig({ agentIdDefault: "copilot-cli" });
    expect(cfg.recallLimit).toBe(7);
    expect(cfg.debug).toBe(true);
  });

  it("`copilot` wins over `claude_code` when both are present", () => {
    writeOv({
      server: { url: "http://example" },
      copilot: { recallLimit: 11 },
      claude_code: { recallLimit: 7 },
    });
    const cfg = loadConfig({ agentIdDefault: "copilot-cli" });
    expect(cfg.recallLimit).toBe(11);
  });

  it("captureMode whitelist: only 'keyword' flips it; everything else -> 'semantic'", () => {
    writeOv({ server: {}, copilot: { captureMode: "weird" } });
    const cfg = loadConfig({ agentIdDefault: "copilot-cli" });
    expect(cfg.captureMode).toBe("semantic");
  });
});

describe("loadConfig — bypass patterns", () => {
  it("env CSV wins over ov.conf array", () => {
    writeOv({ server: {}, copilot: { bypassSessionPatterns: ["/tmp/**"] } });
    process.env["OPENVIKING_BYPASS_SESSION_PATTERNS"] = "/scratch/**, **/throwaway/*";
    const cfg = loadConfig({ agentIdDefault: "copilot-cli" });
    expect(cfg.bypassSessionPatterns).toEqual(["/scratch/**", "**/throwaway/*"]);
  });

  it("ov.conf array survives when env CSV is absent", () => {
    writeOv({
      server: {},
      copilot: { bypassSessionPatterns: ["/tmp/**", "**/scratch/**"] },
    });
    const cfg = loadConfig({ agentIdDefault: "copilot-cli" });
    expect(cfg.bypassSessionPatterns).toEqual(["/tmp/**", "**/scratch/**"]);
  });

  it("hostOverrides patterns win over env and ov.conf", () => {
    writeOv({ server: {}, copilot: { bypassSessionPatterns: ["/tmp/**"] } });
    process.env["OPENVIKING_BYPASS_SESSION_PATTERNS"] = "/scratch/**";
    const cfg = loadConfig({
      agentIdDefault: "copilot-vscode",
      hostOverrides: { bypassSessionPatterns: ["/host/**"] },
    });
    expect(cfg.bypassSessionPatterns).toEqual(["/host/**"]);
  });

  it("OPENVIKING_BYPASS_SESSION env var is honoured (one-shot)", () => {
    process.env["OPENVIKING_BYPASS_SESSION"] = "1";
    const cfg = loadConfig({ agentIdDefault: "copilot-cli" });
    expect(cfg.bypassSession).toBe(true);
  });
});

describe("isPluginEnabled", () => {
  it("disabled by default when no env / no config files", () => {
    expect(isPluginEnabled()).toBe(false);
  });

  it("env var = '0' forces disabled even when files exist", () => {
    writeOv({ server: { url: "http://x" } });
    writeCli({ url: "http://y" });
    process.env["OPENVIKING_MEMORY_ENABLED"] = "0";
    expect(isPluginEnabled()).toBe(false);
  });

  it("env var = '1' forces enabled even with no files", () => {
    process.env["OPENVIKING_MEMORY_ENABLED"] = "1";
    expect(isPluginEnabled()).toBe(true);
  });

  it("enabled when ov.conf exists and copilot.enabled !== false", () => {
    writeOv({ server: { url: "http://x" } });
    expect(isPluginEnabled()).toBe(true);
  });

  it("disabled when ov.conf exists and copilot.enabled === false", () => {
    writeOv({ server: { url: "http://x" }, copilot: { enabled: false } });
    expect(isPluginEnabled()).toBe(false);
  });

  it("legacy claude_code.enabled = false also disables, when copilot block absent", () => {
    writeOv({ server: { url: "http://x" }, claude_code: { enabled: false } });
    expect(isPluginEnabled()).toBe(false);
  });

  it("copilot.enabled overrides claude_code.enabled when both present", () => {
    writeOv({
      server: { url: "http://x" },
      copilot: { enabled: true },
      claude_code: { enabled: false },
    });
    expect(isPluginEnabled()).toBe(true);
  });

  it("ovcli.conf alone is enough to enable", () => {
    writeCli({ url: "http://y", api_key: "k" });
    expect(isPluginEnabled()).toBe(true);
  });
});

describe("loadConfig — clamps and floors", () => {
  it("clamps recallTokenBudget to its floor (200)", () => {
    process.env["OPENVIKING_RECALL_TOKEN_BUDGET"] = "50";
    const cfg = loadConfig({ agentIdDefault: "copilot-cli" });
    expect(cfg.recallTokenBudget).toBe(200);
  });

  it("clamps scoreThreshold into [0, 1]", () => {
    process.env["OPENVIKING_SCORE_THRESHOLD"] = "1.7";
    const cfg = loadConfig({ agentIdDefault: "copilot-cli" });
    expect(cfg.scoreThreshold).toBe(1);
  });

  it("clamps timeoutMs to its floor (1000ms)", () => {
    process.env["OPENVIKING_TIMEOUT_MS"] = "100";
    const cfg = loadConfig({ agentIdDefault: "copilot-cli" });
    expect(cfg.timeoutMs).toBe(1000);
  });
});
