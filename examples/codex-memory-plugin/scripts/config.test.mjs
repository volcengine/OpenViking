import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { loadConfig } from "./config.mjs";
import { isBypassed } from "./shared/session-model.mjs";

// The layers, the knobs and the peer are the shared loader's, and
// memory-plugin-shared/plugin-config.test.mjs holds this harness to them. What
// is left here is the compression switch this harness reads as a boolean, and
// the bypass patterns it is the only harness to resolve from every layer.
const OVERRIDES = [
  "OPENVIKING_CONFIG_FILE",
  "OPENVIKING_CLI_CONFIG_FILE",
  "OPENVIKING_HOME",
  "OPENVIKING_STATE_DIR",
  "OPENVIKING_BYPASS_SESSION",
  "OPENVIKING_BYPASS_SESSION_PATTERNS",
  "OPENVIKING_RECALL_COMPRESS",
  "OPENVIKING_RECALL_COMPRESS_MODEL",
  "OPENVIKING_RECALL_COMPRESS_THINKING",
  "OPENVIKING_URL",
  "OPENVIKING_BASE_URL",
  "OPENVIKING_API_KEY",
  "OPENVIKING_BEARER_TOKEN",
];

/**
 * Run loadConfig() against a throwaway ~/.openviking pair plus a workspace
 * directory holding `.openviking/config.json`. The bare `.git` is what makes
 * that directory a workspace root; `other/` is a second directory with neither.
 */
function withConfigs({ ov, cli, workspace, env = {} }, fn) {
  const dir = mkdtempSync(join(tmpdir(), "cx-config-"));
  const ovPath = join(dir, "ov.conf");
  const cliPath = join(dir, "ovcli.conf");
  const workspaceDir = join(dir, "workspace");
  const otherDir = join(dir, "other");
  const saved = Object.fromEntries(OVERRIDES.map((key) => [key, process.env[key]]));
  try {
    for (const key of OVERRIDES) delete process.env[key];
    if (ov) writeFileSync(ovPath, JSON.stringify(ov));
    if (cli) writeFileSync(cliPath, JSON.stringify(cli));
    mkdirSync(join(workspaceDir, ".openviking"), { recursive: true });
    mkdirSync(join(workspaceDir, ".git"), { recursive: true });
    mkdirSync(otherDir, { recursive: true });
    if (workspace) {
      writeFileSync(join(workspaceDir, ".openviking", "config.json"), JSON.stringify(workspace));
    }
    process.env.OPENVIKING_CONFIG_FILE = ovPath;
    process.env.OPENVIKING_CLI_CONFIG_FILE = cliPath;
    // Keeps the identity cache and the workspace registry out of the real home.
    process.env.OPENVIKING_HOME = join(dir, "home");
    Object.assign(process.env, env);
    return fn({ ovPath, cliPath, workspaceDir, otherDir });
  } finally {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    rmSync(dir, { recursive: true, force: true });
  }
}

test("a repository can carry the bypass patterns its contributors share", () => {
  withConfigs({
    cli: { url: "http://127.0.0.1:1933", api_key: "sk-cli" },
    workspace: { version: 1, bypass: { session_patterns: ["**/workspace"] } },
  }, ({ workspaceDir, otherDir }) => {
    const inside = loadConfig(workspaceDir);
    assert.deepEqual(inside.bypassSessionPatterns, ["**/workspace"]);
    assert.equal(isBypassed(inside, { cwd: workspaceDir }), true);

    const outside = loadConfig(otherDir);
    assert.deepEqual(outside.bypassSessionPatterns, []);
    assert.equal(isBypassed(outside, { cwd: otherDir }), false);
  });
});

test("the bypass env vars override the file, matching every other harness", () => {
  withConfigs({
    ov: { codex: { bypassSessionPatterns: ["/from/ov-conf"] } },
    cli: { url: "http://127.0.0.1:1933", api_key: "sk-cli" },
    env: {
      OPENVIKING_BYPASS_SESSION_PATTERNS: "/from/env, /also/env ,",
      OPENVIKING_BYPASS_SESSION: "1",
    },
  }, ({ otherDir }) => {
    const cfg = loadConfig(otherDir);
    assert.deepEqual(cfg.bypassSessionPatterns, ["/from/env", "/also/env"]);
    assert.equal(cfg.bypassSession, true);
    assert.equal(isBypassed(cfg, { cwd: "/anywhere" }), true, "the switch wins regardless of cwd");
  });
});

test("ov.conf's codex section still supplies the patterns when no env var does", () => {
  withConfigs({
    ov: { codex: { bypassSessionPatterns: ["/scratch/*", 7, "  "] } },
    cli: { url: "http://127.0.0.1:1933", api_key: "sk-cli" },
  }, ({ otherDir }) => {
    const cfg = loadConfig(otherDir);
    assert.deepEqual(cfg.bypassSessionPatterns, ["/scratch/*"]);
    assert.equal(cfg.bypassSession, false);
  });
});

// This harness reads the shared compression knob as a boolean, and calls a
// compressor configured only once it has been told what to run.
test("the compression switch is a boolean here, and a model is what configures one", () => {
  withConfigs({
    cli: { url: "http://127.0.0.1:1933", api_key: "sk-cli", plugin: { recallCompress: "auto" } },
  }, ({ otherDir }) => {
    const cfg = loadConfig(otherDir);
    assert.equal(cfg.recallCompress, true, "\"auto\" is the Claude Code spelling of on");
    assert.equal(cfg.recallCompressConfigured, false, "naming the switch is not naming a compressor");
  });

  withConfigs({
    cli: { url: "http://127.0.0.1:1933", api_key: "sk-cli", plugin: { recallCompress: "off" } },
  }, ({ otherDir }) => {
    assert.equal(loadConfig(otherDir).recallCompress, false);
  });

  withConfigs({
    cli: {
      url: "http://127.0.0.1:1933",
      api_key: "sk-cli",
      plugin: { codex: { recallCompressModel: "gpt-5.5" } },
    },
  }, ({ otherDir }) => {
    assert.equal(loadConfig(otherDir).recallCompressConfigured, true);
  });
});
