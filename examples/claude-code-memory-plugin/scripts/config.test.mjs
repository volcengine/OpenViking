import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { loadConfig } from "./config.mjs";

// The layers, the knobs and the peer are the shared loader's, and
// memory-plugin-shared/plugin-config.test.mjs holds this harness to them. What
// is left here is what this harness answers itself: which file the credential
// came from, and the tri-state digest mode.
const OVERRIDES = [
  "OPENVIKING_CONFIG_FILE",
  "OPENVIKING_CLI_CONFIG_FILE",
  "OPENVIKING_HOME",
  "OPENVIKING_STATE_DIR",
  "OPENVIKING_API_KEY",
  "OPENVIKING_BEARER_TOKEN",
  "OPENVIKING_RECALL_COMPRESS",
  "OPENVIKING_RECALL_REWRITE",
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
    // Keeps the identity cache and the workspace registry out of the real home.
    process.env.OPENVIKING_HOME = join(dir, "home");
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
    assert.equal(cfg.apiKeySource, "ovcli");
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
    assert.equal(cfg.apiKeySource, "ov");
    assert.equal(cfg.credentialPath, ovPath);
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
    assert.equal(cfg.apiKeySource, "env");
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
    assert.equal(cfg.apiKeySource, "none");
    assert.equal(cfg.credentialPath, null);
  });
});

// `recallRewrite` is a tri-state this harness reads from the shared on/off
// switch, so a mode the switch cannot express still has to survive the trip.
test("the digest mode is this harness's own tri-state, under either env name", () => {
  withConfigs({
    ov: { server: {} },
    cli: { url: "http://127.0.0.1:1933", api_key: "sk-cli", plugin: { recallCompress: "server" } },
  }, () => {
    assert.equal(loadConfig().recallRewrite, "server");
  });

  withConfigs({
    ov: { server: {} },
    cli: { url: "http://127.0.0.1:1933", api_key: "sk-cli", plugin: { recallCompress: "louder" } },
  }, () => {
    assert.equal(loadConfig().recallRewrite, "auto", "an unreadable mode falls back to auto");
  });

  withConfigs({
    ov: { server: {} },
    cli: { url: "http://127.0.0.1:1933", api_key: "sk-cli", plugin: { recallCompress: "off" } },
    env: { OPENVIKING_RECALL_REWRITE: "client" },
  }, () => {
    assert.equal(loadConfig().recallRewrite, "client", "the older env spelling still works");
  });
});
