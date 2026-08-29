import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { loadConfig } from "./config.mjs";

const OVERRIDES = [
  "OPENVIKING_CONFIG_FILE",
  "OPENVIKING_CLI_CONFIG_FILE",
  "OPENVIKING_API_KEY",
  "OPENVIKING_BEARER_TOKEN",
];

/**
 * Run loadConfig() against a throwaway ~/.openviking pair, with the env vars
 * that feed the credential chain reset to exactly what the case needs.
 */
function withConfigs({ ov, cli, env = {} }, fn) {
  const dir = mkdtempSync(join(tmpdir(), "cc-config-"));
  const ovPath = join(dir, "ov.conf");
  const cliPath = join(dir, "ovcli.conf");
  const saved = Object.fromEntries(OVERRIDES.map((key) => [key, process.env[key]]));
  try {
    for (const key of OVERRIDES) delete process.env[key];
    if (ov) writeFileSync(ovPath, JSON.stringify(ov));
    if (cli) writeFileSync(cliPath, JSON.stringify(cli));
    process.env.OPENVIKING_CONFIG_FILE = ovPath;
    process.env.OPENVIKING_CLI_CONFIG_FILE = cliPath;
    Object.assign(process.env, env);
    return fn({ ovPath, cliPath });
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
