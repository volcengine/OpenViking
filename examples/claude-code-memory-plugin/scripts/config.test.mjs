import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { loadConfig } from "./config.mjs";

const OVERRIDES = [
  "OPENVIKING_CONFIG_FILE",
  "OPENVIKING_CLI_CONFIG_FILE",
  "OPENVIKING_HOME",
  "OPENVIKING_STATE_DIR",
  "OPENVIKING_PEER_ID",
  "OPENVIKING_AUTO_CAPTURE",
  "OPENVIKING_API_KEY",
  "OPENVIKING_BEARER_TOKEN",
];

/**
 * Run loadConfig() against a throwaway ~/.openviking pair, with the env vars
 * that feed the credential chain reset to exactly what the case needs.
 *
 * `workspace` is written to `workspace/.openviking/config.json`; the bare
 * `.git` beside it is what makes that directory a workspace root, and
 * `other/` is a second directory with neither.
 */
function withConfigs({ ov, cli, workspace, env = {} }, fn) {
  const dir = mkdtempSync(join(tmpdir(), "cc-config-"));
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

test("an ovcli.conf api_key is reported as the credential source", () => {
  withConfigs({
    ov: { server: { host: "127.0.0.1", port: 1933 } },
    cli: { url: "http://127.0.0.1:1933", api_key: "sk-cli" },
  }, ({ ovPath, cliPath }) => {
    const cfg = loadConfig();
    assert.equal(cfg.apiKey, "sk-cli");
    assert.equal(cfg.credentialSource, "ovcli");
    assert.equal(cfg.credentialPath, cliPath);
    // configPath stays "whichever file parsed" for backward compat.
    assert.equal(cfg.configPath, ovPath);
  });
});

test("an ov.conf root_api_key is reported even when ovcli.conf exists", () => {
  withConfigs({
    ov: { server: { root_api_key: "sk-root" } },
    cli: { url: "http://127.0.0.1:1933" },
  }, ({ ovPath }) => {
    const cfg = loadConfig();
    assert.equal(cfg.apiKey, "sk-root");
    assert.equal(cfg.credentialSource, "ov");
    assert.equal(cfg.credentialPath, ovPath);
  });
});

test("an ov.conf claude_code.apiKey is reported as ov.conf", () => {
  withConfigs({
    ov: { claude_code: { apiKey: "sk-cc" } },
    cli: { url: "http://127.0.0.1:1933" },
  }, ({ ovPath }) => {
    const cfg = loadConfig();
    assert.equal(cfg.apiKey, "sk-cc");
    assert.equal(cfg.credentialSource, "ov");
    assert.equal(cfg.credentialPath, ovPath);
  });
});

test("an ovcli.conf plugin.claude_code.apiKey is reported as ovcli.conf", () => {
  withConfigs({
    ov: { server: {} },
    cli: { url: "http://127.0.0.1:1933", plugin: { claude_code: { apiKey: "sk-plugin" } } },
  }, ({ cliPath }) => {
    const cfg = loadConfig();
    assert.equal(cfg.apiKey, "sk-plugin");
    assert.equal(cfg.credentialSource, "ovcli");
    assert.equal(cfg.credentialPath, cliPath);
  });
});

test("an env api_key wins and carries no path", () => {
  withConfigs({
    ov: { server: { root_api_key: "sk-root" } },
    cli: { url: "http://127.0.0.1:1933", api_key: "sk-cli" },
    env: { OPENVIKING_API_KEY: "sk-env" },
  }, () => {
    const cfg = loadConfig();
    assert.equal(cfg.apiKey, "sk-env");
    assert.equal(cfg.credentialSource, "env");
    assert.equal(cfg.credentialPath, null);
  });
});

test("the workspace layer follows the cwd it is handed, not the process's", () => {
  withConfigs({
    ov: { server: {} },
    cli: { url: "http://127.0.0.1:1933", api_key: "sk-cli" },
    workspace: { version: 1, peer: { id: "team-a" }, capture: { enabled: false } },
  }, ({ workspaceDir, otherDir }) => {
    const inside = loadConfig(workspaceDir);
    assert.equal(inside.peerId, "team-a");
    assert.equal(inside.autoCapture, false);

    const outside = loadConfig(otherDir);
    assert.equal(outside.peerId, "");
    assert.equal(outside.autoCapture, true);
  });
});

test("an omitted cwd falls back to this process's directory", () => {
  withConfigs({
    ov: { server: {} },
    cli: { url: "http://127.0.0.1:1933", api_key: "sk-cli" },
    workspace: { version: 1, capture: { enabled: false } },
  }, ({ workspaceDir }) => {
    const origin = process.cwd();
    try {
      process.chdir(workspaceDir);
      assert.equal(loadConfig().autoCapture, false);
      assert.equal(loadConfig("").autoCapture, false);
    } finally {
      process.chdir(origin);
    }
  });
});

test("no api_key anywhere reports no source", () => {
  withConfigs({
    ov: { server: { host: "127.0.0.1" } },
    cli: { url: "http://127.0.0.1:1933" },
  }, () => {
    const cfg = loadConfig();
    assert.equal(cfg.apiKey, "");
    assert.equal(cfg.credentialSource, "none");
    assert.equal(cfg.credentialPath, null);
  });
});
