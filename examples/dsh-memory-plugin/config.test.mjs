import assert from "node:assert/strict";
import test from "node:test";
import { resolveConfig } from "./config.mjs";

// The layers, the knobs and the peer are the shared loader's, and
// memory-plugin-shared/plugin-config.test.mjs holds this harness to them. What
// is left here is the cordis input: the host hands this plugin its environment
// and its settings, and no other harness has a layer like it.

test("behavior environment overrides are applied and normalized", () => {
  const config = resolveConfig({}, {
    OPENVIKING_CLI_CONFIG_FILE: "/nonexistent/ovcli.conf",
    OPENVIKING_URL: "http://127.0.0.1:19464/",
    OPENVIKING_WORKSPACE_PEER: "0",
    OPENVIKING_RECALL_PEER_SCOPE: "actor",
    OPENVIKING_RECALL_QUERY_EXPANSION: "off",
    OPENVIKING_RECALL_LIMIT: "7",
  }, "/workspace/project");

  assert.equal(config.endpoint, "http://127.0.0.1:19464");
  assert.equal(config.workspacePeer, false);
  assert.equal(config.peerId, "");
  assert.equal(config.recallPeerScope, "actor");
  assert.equal(config.recallQueryExpansion, "off");
  assert.equal(config.recallQueryExpansionConfigured, true);
  assert.equal(config.recallLimit, 7);
  assert.equal(config.recallLimitConfigured, true);
});

test("explicit plugin config overrides credential files", () => {
  const config = resolveConfig({
    endpoint: "http://plugin.local",
    apiKey: "plugin-key",
    account: "plugin-account",
    user: "plugin-user",
    peerId: "plugin-peer",
  }, {
    OPENVIKING_CLI_CONFIG_FILE: "/nonexistent/ovcli.conf",
    OPENVIKING_URL: "http://env.local",
    OPENVIKING_API_KEY: "env-key",
    OPENVIKING_ACCOUNT: "env-account",
    OPENVIKING_USER: "env-user",
    OPENVIKING_PEER_ID: "env-peer",
  }, "/workspace/project");

  assert.equal(config.endpoint, "http://plugin.local");
  assert.equal(config.apiKey, "plugin-key");
  assert.equal(config.account, "plugin-account");
  assert.equal(config.user, "plugin-user");
  assert.equal(config.peerId, "plugin-peer");
});

// A knob set once in ovcli.conf reaches this harness like any other, and the
// host's own input is the lower layer.
test("the ovcli.conf plugin section outranks the cordis input", async () => {
  const { mkdtempSync, writeFileSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const dir = mkdtempSync(join(tmpdir(), "dsh-plugin-conf-"));
  const cliPath = join(dir, "ovcli.conf");
  writeFileSync(cliPath, JSON.stringify({
    url: "http://127.0.0.1:1933",
    plugin: { recallLimit: 5, dsh: { captureMode: "keyword" } },
  }));

  const config = resolveConfig({ recallLimit: 3, captureMode: "semantic" }, {
    OPENVIKING_CLI_CONFIG_FILE: cliPath,
  }, "/workspace/project");

  assert.equal(config.recallLimit, 5);
  assert.equal(config.captureMode, "keyword");
});

// This harness has no ov.conf section of its own in the layer stack — the
// cordis input occupies it — so the section is merged in explicitly.
test("ov.conf's dsh section is the lowest layer, under the cordis input", async () => {
  const { mkdtempSync, writeFileSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const dir = mkdtempSync(join(tmpdir(), "dsh-ov-conf-"));
  const ovPath = join(dir, "ov.conf");
  writeFileSync(ovPath, JSON.stringify({
    server: { root_api_key: "root-key" },
    codex: { apiKey: "sk-codex", recallLimit: 9 },
    dsh: { apiKey: "sk-dsh", recallLimit: 4, scoreThreshold: 0.6 },
  }));
  const env = {
    OPENVIKING_CONFIG_FILE: ovPath,
    OPENVIKING_CLI_CONFIG_FILE: join(dir, "absent-ovcli.conf"),
  };

  const config = resolveConfig({ recallLimit: 3 }, env, "/workspace/project");
  assert.equal(config.apiKey, "sk-dsh");
  assert.equal(config.recallLimit, 3);
  assert.equal(config.scoreThreshold, 0.6);
});

// The credential chain used to overwrite the peer rather than supply it, so an
// ovcli.conf naming no actor peer erased one configured for this harness.
test("a configured peer survives an empty credential peer", async () => {
  const { mkdtempSync, writeFileSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const dir = mkdtempSync(join(tmpdir(), "dsh-peer-"));
  const cliPath = join(dir, "ovcli.conf");
  writeFileSync(cliPath, JSON.stringify({
    url: "http://127.0.0.1:1933",
    plugin: { dsh: { peerId: "dsh-peer" } },
  }));

  const config = resolveConfig({}, {
    OPENVIKING_CONFIG_FILE: join(dir, "absent-ov.conf"),
    OPENVIKING_CLI_CONFIG_FILE: cliPath,
  }, "/workspace/project");

  assert.equal(config.peerId, "dsh-peer");
  assert.equal(config.explicitPeerId, "dsh-peer");
});
