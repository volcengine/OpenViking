import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { loadConfig } from "../config.ts";
import { isBypassed } from "../shared/session-model.mjs";

// The layers, the knobs and the peer order are the shared loader's, and
// memory-plugin-shared/plugin-config.test.mjs holds this harness to them. What
// is left here is what this extension answers itself: the takeover knobs, the
// peer it reports from the workspace resolution, and the two names it kept from
// before it shared a loader.

/**
 * Run `loadConfig()` with `body` as the extension's `plugin.pi` section of a
 * throwaway ovcli.conf. `cliConfig` adds the connection fields around it.
 */
async function withPluginSection(body, fn, env = {}, cliConfig = null) {
  const dir = await mkdtemp(join(tmpdir(), "ov-pi-config-用户-"));
  const oldEnv = {
    OPENVIKING_URL: process.env.OPENVIKING_URL,
    OPENVIKING_API_KEY: process.env.OPENVIKING_API_KEY,
    OPENVIKING_ACCOUNT: process.env.OPENVIKING_ACCOUNT,
    OPENVIKING_USER: process.env.OPENVIKING_USER,
    OPENVIKING_PEER_ID: process.env.OPENVIKING_PEER_ID,
    OPENVIKING_WORKSPACE_PEER: process.env.OPENVIKING_WORKSPACE_PEER,
    OPENVIKING_RECALL_PEER_SCOPE: process.env.OPENVIKING_RECALL_PEER_SCOPE,
    OPENVIKING_CREDENTIAL_SOURCE: process.env.OPENVIKING_CREDENTIAL_SOURCE,
    OPENVIKING_CLI_CONFIG_FILE: process.env.OPENVIKING_CLI_CONFIG_FILE,
    OPENVIKING_CONFIG_FILE: process.env.OPENVIKING_CONFIG_FILE,
    OPENVIKING_DEBUG_LOG: process.env.OPENVIKING_DEBUG_LOG,
    OV_DEBUG_LOG: process.env.OV_DEBUG_LOG,
    OPENVIKING_BYPASS_SESSION: process.env.OPENVIKING_BYPASS_SESSION,
    OPENVIKING_BYPASS_SESSION_PATTERNS: process.env.OPENVIKING_BYPASS_SESSION_PATTERNS,
  };
  process.env.OPENVIKING_CREDENTIAL_SOURCE = "env";
  process.env.OPENVIKING_URL = "http://127.0.0.1:1933";
  process.env.OPENVIKING_CLI_CONFIG_FILE = join(dir, "ovcli.conf");
  process.env.OPENVIKING_CONFIG_FILE = join(dir, "ov.conf");
  delete process.env.OPENVIKING_API_KEY;
  delete process.env.OPENVIKING_ACCOUNT;
  delete process.env.OPENVIKING_USER;
  delete process.env.OPENVIKING_PEER_ID;
  delete process.env.OPENVIKING_WORKSPACE_PEER;
  delete process.env.OPENVIKING_RECALL_PEER_SCOPE;
  delete process.env.OPENVIKING_DEBUG_LOG;
  delete process.env.OV_DEBUG_LOG;
  delete process.env.OPENVIKING_BYPASS_SESSION;
  delete process.env.OPENVIKING_BYPASS_SESSION_PATTERNS;
  for (const [key, value] of Object.entries(env)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }

  try {
    await writeFile(
      join(dir, "ovcli.conf"),
      JSON.stringify({ ...(cliConfig || {}), plugin: { pi: body } }),
      "utf8",
    );
    return await fn(loadConfig(), dir);
  } finally {
    for (const [key, value] of Object.entries(oldEnv)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    await rm(dir, { recursive: true, force: true });
  }
}

test("loadConfig defaults takeover on", async () => {
  await withPluginSection({}, (cfg) => {
    assert.equal(cfg.takeoverEnabled, true);
    assert.equal(cfg.takeoverTokenThreshold, 30000);
    assert.equal(cfg.takeoverKeepRecentTurns, 3);
    assert.equal(cfg.takeoverOverviewBudget, 3000);
    assert.equal(cfg.takeoverOverviewPollMs, 2000);
    assert.equal(cfg.takeoverOverviewPollMax, 15);
  });
});

test("loadConfig reads the takeover knobs", async () => {
  await withPluginSection({
    takeoverEnabled: false,
    takeoverTokenThreshold: 600,
    takeoverKeepRecentTurns: 1,
    takeoverOverviewBudget: 1200,
    takeoverOverviewPollMs: 10,
    takeoverOverviewPollMax: 2,
  }, (cfg) => {
    assert.equal(cfg.takeoverEnabled, false);
    assert.equal(cfg.takeoverTokenThreshold, 600);
    assert.equal(cfg.takeoverKeepRecentTurns, 1);
    assert.equal(cfg.takeoverOverviewBudget, 1200);
    assert.equal(cfg.takeoverOverviewPollMs, 10);
    assert.equal(cfg.takeoverOverviewPollMax, 2);
  });
});

test("loadConfig clamps invalid takeover values", async () => {
  await withPluginSection({
    takeoverEnabled: "no",
    takeoverTokenThreshold: -1,
    takeoverKeepRecentTurns: -5,
    takeoverOverviewBudget: 1,
    takeoverOverviewPollMs: -2,
    takeoverOverviewPollMax: 0,
  }, (cfg) => {
    assert.equal(cfg.takeoverEnabled, false, "\"no\" is a recognised off");
    assert.equal(cfg.takeoverTokenThreshold, 1);
    assert.equal(cfg.takeoverKeepRecentTurns, 0);
    assert.equal(cfg.takeoverOverviewBudget, 100);
    assert.equal(cfg.takeoverOverviewPollMs, 0);
    assert.equal(cfg.takeoverOverviewPollMax, 1);
  });
});

test("loadConfig derives workspace peer by default", async () => {
  // The default is now the repository, so the expectation follows whichever
  // template resolves where the suite runs — inside a checkout that is the
  // remote, and outside one it is still the old working-directory id.
  const { resolveEffectivePeerId } = await import("../shared/workspace-peer.mjs");
  const expected = resolveEffectivePeerId({ cfg: {}, cwd: process.cwd() });
  await withPluginSection({}, (cfg) => {
    assert.equal(cfg.peerId, expected.peerId);
    assert.equal(cfg.workspacePeer, true);
    assert.equal(cfg.recallPeerScope, "all");
  });
});

// This extension reports the whole workspace resolution's peer rather than the
// one the layers named, so both directions of that have to hold.
test("loadConfig prefers the plugin section peer over workspace derivation", async () => {
  await withPluginSection({
    peerId: " pi ",
    workspacePeer: true,
  }, (cfg) => {
    assert.equal(cfg.peerId, "pi");
  });
});

test("loadConfig still honours the deprecated OV_DEBUG_LOG", async () => {
  await withPluginSection({}, (cfg) => {
    assert.equal(cfg.debugLogPath, "/tmp/ov-pi-legacy.log");
  }, { OV_DEBUG_LOG: "/tmp/ov-pi-legacy.log" });
});

test("loadConfig prefers OPENVIKING_DEBUG_LOG over the deprecated alias", async () => {
  await withPluginSection({ debugLogPath: "/tmp/ov-pi-file.log" }, (cfg) => {
    assert.equal(cfg.debugLogPath, "/tmp/ov-pi-shared.log");
  }, {
    OPENVIKING_DEBUG_LOG: "/tmp/ov-pi-shared.log",
    OV_DEBUG_LOG: "/tmp/ov-pi-legacy.log",
  });
});

test("loadConfig falls back to the plugin section debug log path", async () => {
  await withPluginSection({ debugLogPath: " /tmp/ov-pi-file.log " }, (cfg) => {
    assert.equal(cfg.debugLogPath, "/tmp/ov-pi-file.log");
  });
});

test("loadConfig projects bypassPatterns onto the name the shared matcher reads", async () => {
  await withPluginSection({ bypassPatterns: ["/tmp/scratch*", " "] }, (cfg) => {
    assert.deepEqual(cfg.bypassSessionPatterns, ["/tmp/scratch*"]);
    assert.deepEqual(cfg.bypassPatterns, ["/tmp/scratch*"]);
    assert.equal(isBypassed(cfg, { cwd: "/tmp/scratch-1" }), true);
    assert.equal(isBypassed(cfg, { cwd: "/tmp/keep" }), false);
  });
});

test("loadConfig reads bypassSessionPatterns directly and lets the env override it", async () => {
  await withPluginSection({ bypassSessionPatterns: ["/from/file"] }, (cfg) => {
    assert.deepEqual(cfg.bypassSessionPatterns, ["/from/file"]);
  });

  await withPluginSection({ bypassSessionPatterns: ["/from/file"] }, (cfg) => {
    assert.deepEqual(cfg.bypassSessionPatterns, ["/from/env", "/also/env"]);
    assert.equal(cfg.bypassSession, true);
    assert.equal(isBypassed(cfg, { cwd: "/anywhere" }), true, "the switch wins regardless of cwd");
  }, {
    OPENVIKING_BYPASS_SESSION_PATTERNS: "/from/env, /also/env ,",
    OPENVIKING_BYPASS_SESSION: "1",
  });
});
