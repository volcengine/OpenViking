import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile, readFile } from "node:fs/promises";
import { join, dirname } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath, pathToFileURL } from "node:url";
import { loadConfig, loadConfigFromModuleUrl } from "../config.ts";

const EXTENSION_DIR = dirname(dirname(fileURLToPath(import.meta.url)));

const CONTEXT_WINDOW_DEFAULTS = {
  resetDeadlineMs: 60000,
  archivePollMs: 2000,
  overviewRefreshMaxAttempts: 20,
  overviewBudget: 3000,
  notesBudget: 1500,
  pendingRequestBudget: 400,
  softPercent: 70,
  hardPercent: 85,
  idleGapMinutes: 30,
  statusEveryTurn: true,
  historyItemMaxChars: 8000,
  recentResetGuardMs: 60000,
};

async function withConfigFile(body, fn, env = {}, cliConfig = null) {
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
    OPENVIKING_CONTEXT_STATUS_EVERY_TURN: process.env.OPENVIKING_CONTEXT_STATUS_EVERY_TURN,
    OPENVIKING_CONTEXT_RESET_DEADLINE_MS: process.env.OPENVIKING_CONTEXT_RESET_DEADLINE_MS,
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
  delete process.env.OPENVIKING_CONTEXT_STATUS_EVERY_TURN;
  delete process.env.OPENVIKING_CONTEXT_RESET_DEADLINE_MS;
  for (const [key, value] of Object.entries(env)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }

  try {
    await writeFile(join(dir, "config.json"), JSON.stringify(body), "utf8");
    if (cliConfig !== null) {
      await writeFile(join(dir, "ovcli.conf"), JSON.stringify(cliConfig), "utf8");
    }
    return await fn(loadConfig(dir), dir);
  } finally {
    for (const [key, value] of Object.entries(oldEnv)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    await rm(dir, { recursive: true, force: true });
  }
}

test("loadConfig defaults capture to faithful, tool results on", async () => {
  await withConfigFile({}, (cfg) => {
    assert.equal(cfg.faithfulCapture, true);
    assert.equal(cfg.captureToolResults, true);
    assert.equal(cfg.captureAssistantTurns, true);
    assert.equal(cfg.captureMaxLength, 24000);
    assert.equal(cfg.captureToolMaxChars, 1000000);
  });
});

test("loadConfig carries no takeover or recall-ledger keys", async () => {
  // The fork dropped both features; a stale key in an old config.json is
  // passed through untouched but never read, so the defaults must not name it.
  await withConfigFile({}, (cfg) => {
    for (const key of Object.keys(cfg)) {
      assert.ok(!key.startsWith("takeover"), `unexpected takeover key: ${key}`);
    }
    assert.equal(cfg.recallLedger, undefined);
  });
});

test("loadConfig lets config.json turn tool-result capture off", async () => {
  await withConfigFile({ captureToolResults: false }, (cfg) => {
    assert.equal(cfg.captureToolResults, false);
    // faithfulCapture is not a switch: the archive is the only way back to a
    // closed window.
    assert.equal(cfg.faithfulCapture, true);
  });
});

test("loadConfigFromModuleUrl decodes Unicode paths", async () => {
  await withConfigFile({ commitKeepRecentCount: 3 }, (_cfg, dir) => {
    const moduleUrl = pathToFileURL(join(dir, "index.ts")).href;
    const cfg = loadConfigFromModuleUrl(moduleUrl);
    assert.equal(cfg.commitKeepRecentCount, 3);
  });
});

test("loadConfig clamps invalid values", async () => {
  await withConfigFile({
    commitKeepRecentCount: -5,
    captureMaxLength: 1,
    captureToolMaxChars: 10,
    recallLimit: 999,
    scoreThreshold: 5,
  }, (cfg) => {
    assert.equal(cfg.commitKeepRecentCount, 0);
    assert.equal(cfg.captureMaxLength, 200);
    assert.equal(cfg.captureToolMaxChars, 200);
    assert.equal(cfg.recallLimit, 50);
    assert.equal(cfg.scoreThreshold, 1);
  });
});

test("loadConfig derives workspace peer by default", async () => {
  // The default is now the repository, so the expectation follows whichever
  // template resolves where the suite runs — inside a checkout that is the
  // remote, and outside one it is still the old working-directory id.
  const { resolveEffectivePeerId } = await import("../shared/workspace-peer.mjs");
  const expected = resolveEffectivePeerId({ cfg: {}, cwd: process.cwd() });
  await withConfigFile({}, (cfg) => {
    assert.equal(cfg.peerId, expected.peerId);
    assert.equal(cfg.workspacePeer, true);
    assert.equal(cfg.recallPeerScope, "all");
  });
});

test("loadConfig prefers config peer over workspace derivation", async () => {
  await withConfigFile({
    peerId: " pi ",
    workspacePeer: true,
  }, (cfg) => {
    assert.equal(cfg.peerId, "pi");
  });
});

test("loadConfig keeps config peer when workspace derivation is disabled", async () => {
  await withConfigFile({
    peerId: "pi",
    workspacePeer: false,
  }, (cfg) => {
    assert.equal(cfg.peerId, "pi");
    assert.equal(cfg.workspacePeer, false);
  });
});

test("loadConfig gives environment peer precedence over config peer", async () => {
  await withConfigFile({
    peerId: "config-peer",
    recallPeerScope: "actor",
    workspacePeer: false,
  }, (cfg) => {
    assert.equal(cfg.peerId, "explicit-peer");
    assert.equal(cfg.workspacePeer, false);
    assert.equal(cfg.recallPeerScope, "actor");
  }, { OPENVIKING_PEER_ID: "explicit-peer" });
});

test("loadConfig leaves the debug log off when nothing asks for it", async () => {
  await withConfigFile({}, (cfg) => {
    assert.equal(cfg.debugLogPath, "");
  });
});

test("loadConfig reads the debug log path from OPENVIKING_DEBUG_LOG", async () => {
  await withConfigFile({}, (cfg) => {
    assert.equal(cfg.debugLogPath, "/tmp/ov-pi-shared.log");
  }, { OPENVIKING_DEBUG_LOG: "/tmp/ov-pi-shared.log" });
});

test("loadConfig still honours the deprecated OV_DEBUG_LOG", async () => {
  await withConfigFile({}, (cfg) => {
    assert.equal(cfg.debugLogPath, "/tmp/ov-pi-legacy.log");
  }, { OV_DEBUG_LOG: "/tmp/ov-pi-legacy.log" });
});

test("loadConfig prefers OPENVIKING_DEBUG_LOG over the deprecated alias", async () => {
  await withConfigFile({ debugLogPath: "/tmp/ov-pi-file.log" }, (cfg) => {
    assert.equal(cfg.debugLogPath, "/tmp/ov-pi-shared.log");
  }, {
    OPENVIKING_DEBUG_LOG: "/tmp/ov-pi-shared.log",
    OV_DEBUG_LOG: "/tmp/ov-pi-legacy.log",
  });
});

test("loadConfig falls back to the config file debug log path", async () => {
  await withConfigFile({ debugLogPath: " /tmp/ov-pi-file.log " }, (cfg) => {
    assert.equal(cfg.debugLogPath, "/tmp/ov-pi-file.log");
  });
});

test("loadConfig gives ovcli peer precedence over config peer", async () => {
  await withConfigFile({
    peerId: "config-peer",
  }, (cfg) => {
    assert.equal(cfg.peerId, "ovcli-peer");
  }, {
    OPENVIKING_CREDENTIAL_SOURCE: "cli",
    OPENVIKING_URL: undefined,
  }, {
    url: "http://127.0.0.1:1933",
    actor_peer_id: "ovcli-peer",
  });
});

test("loadConfig ships the contextWindow defaults", async () => {
  await withConfigFile({}, (cfg) => {
    assert.deepEqual(cfg.contextWindow, CONTEXT_WINDOW_DEFAULTS);
  });
});

test("the shipped config.json parses and restates the defaults exactly", async () => {
  // config.json spells every default out, so it silently overrides config.ts
  // once the two drift apart. Load the real file to keep them tied together.
  const shipped = JSON.parse(await readFile(join(EXTENSION_DIR, "config.json"), "utf8"));
  assert.ok(!("captureMode" in shipped), "the dead captureMode key is gone from config.json");
  await withConfigFile(shipped, (cfg) => {
    assert.deepEqual(cfg.contextWindow, CONTEXT_WINDOW_DEFAULTS);
    assert.equal(cfg.captureToolResults, true);
    assert.equal(cfg.captureMaxLength, 24000);
    assert.equal(cfg.captureToolMaxChars, 1000000);
    assert.equal(cfg.commitKeepRecentCount, 10);
    // Nothing in this fork reads them any more: `new_context` and the pi
    // compaction fallback are the only writers of archive boundaries.
    assert.equal("commitTokenThreshold" in shipped, false);
    assert.equal("resumeContextBudget" in shipped, false);
    // config.json must not name recallLimit or recallQueryExpansion: both
    // carry a "…Configured" flag that recall reads as "the user chose this".
    assert.equal(cfg.recallLimitConfigured, false);
    assert.equal(cfg.recallQueryExpansionConfigured, false);
  });
});

test("loadConfig never writes the clamped window back into the module defaults", async () => {
  // The window object is rebuilt per call and clamped in place; if it ever
  // aliased DEFAULT_CONFIG.contextWindow, the hardPercent lift below would
  // leak into every later load in the same process.
  await withConfigFile({ contextWindow: { softPercent: 90, hardPercent: 20 } }, (cfg) => {
    assert.equal(cfg.contextWindow.hardPercent, 90);
  });
  await withConfigFile({}, (cfg) => {
    assert.deepEqual(cfg.contextWindow, CONTEXT_WINDOW_DEFAULTS);
  });
});

test("loadConfig drops the dead captureMode key", async () => {
  // Capture is always faithful in this fork, so nothing reads a
  // semantic/keyword switch and the defaults must not name one.
  await withConfigFile({}, (cfg) => {
    assert.ok(!("captureMode" in cfg), "captureMode is gone from the defaults");
    assert.equal(cfg.captureMaxLength, 24000);
    assert.equal(cfg.captureToolMaxChars, 1000000);
    assert.equal(cfg.captureToolResults, true);
  });
});

test("loadConfig merges contextWindow over the defaults key by key", async () => {
  await withConfigFile({
    contextWindow: {
      resetDeadlineMs: 120000,
      notesBudget: 900,
      statusEveryTurn: false,
      unknownKey: "ignored",
    },
  }, (cfg) => {
    assert.equal(cfg.contextWindow.resetDeadlineMs, 120000);
    assert.equal(cfg.contextWindow.notesBudget, 900);
    assert.equal(cfg.contextWindow.statusEveryTurn, false);
    assert.ok(!("unknownKey" in cfg.contextWindow), "unknown keys are ignored");
    // Untouched keys keep their defaults.
    assert.equal(cfg.contextWindow.archivePollMs, 2000);
    assert.equal(cfg.contextWindow.softPercent, 70);
    assert.equal(cfg.contextWindow.hardPercent, 85);
    assert.equal(cfg.contextWindow.historyItemMaxChars, 8000);
  });
});

test("loadConfig ignores a non-object contextWindow", async () => {
  for (const block of ["yes", null, 7, false, []]) {
    await withConfigFile({ contextWindow: block }, (cfg) => {
      assert.deepEqual(cfg.contextWindow, CONTEXT_WINDOW_DEFAULTS, `contextWindow: ${JSON.stringify(block)}`);
    });
  }
});

const CONTEXT_WINDOW_BOUNDS = [
  { key: "resetDeadlineMs", min: 5000, max: 600000 },
  { key: "archivePollMs", min: 250, max: 30000 },
  { key: "overviewRefreshMaxAttempts", min: 0, max: 200 },
  { key: "overviewBudget", min: 100, max: 50000 },
  { key: "notesBudget", min: 100, max: 20000 },
  { key: "pendingRequestBudget", min: 0, max: 8000 },
  { key: "softPercent", min: 10, max: 99, withHigh: { hardPercent: 99 } },
  { key: "hardPercent", min: 10, max: 99, withLow: { softPercent: 10 } },
  { key: "idleGapMinutes", min: 0, max: 1440 },
  { key: "historyItemMaxChars", min: 500, max: 100000 },
  { key: "recentResetGuardMs", min: 0, max: 600000 },
];

for (const { key, min, max, withLow, withHigh } of CONTEXT_WINDOW_BOUNDS) {
  test(`loadConfig clamps contextWindow.${key} to ${min}..${max}`, async () => {
    await withConfigFile({ contextWindow: { [key]: -1000000, ...(withLow || {}) } }, (cfg) => {
      assert.equal(cfg.contextWindow[key], min);
    });
    await withConfigFile({ contextWindow: { [key]: 100000000, ...(withHigh || {}) } }, (cfg) => {
      assert.equal(cfg.contextWindow[key], max);
    });
  });
}

test("loadConfig falls back to the default for a non-numeric contextWindow value", async () => {
  await withConfigFile({
    contextWindow: { resetDeadlineMs: "soon", overviewBudget: null, idleGapMinutes: {} },
  }, (cfg) => {
    assert.equal(cfg.contextWindow.resetDeadlineMs, 60000);
    // null means "not set" for `??`, so it falls back before the clamp sees it.
    assert.equal(cfg.contextWindow.overviewBudget, 3000);
    assert.equal(cfg.contextWindow.idleGapMinutes, 30);
  });
});

test("loadConfig pulls a coercible junk value to the nearest bound", async () => {
  // `Number("")`, `Number([])` and `Number(true)` are 0 and 1, not NaN, so
  // these never reach the fallback — they land on the minimum. Pinned because
  // it is the one place the README's "falls back to the default" does not hold.
  await withConfigFile({
    contextWindow: { overviewBudget: "", notesBudget: [], archivePollMs: true },
  }, (cfg) => {
    assert.equal(cfg.contextWindow.overviewBudget, 100);
    assert.equal(cfg.contextWindow.notesBudget, 100);
    assert.equal(cfg.contextWindow.archivePollMs, 250);
  });
});

test("loadConfig accepts a numeric string from config.json", async () => {
  await withConfigFile({ contextWindow: { resetDeadlineMs: "120000" } }, (cfg) => {
    assert.equal(cfg.contextWindow.resetDeadlineMs, 120000);
  });
});

test("loadConfig rounds fractional contextWindow values", async () => {
  await withConfigFile({ contextWindow: { archivePollMs: 2500.6, softPercent: 60.4 } }, (cfg) => {
    assert.equal(cfg.contextWindow.archivePollMs, 2501);
    assert.equal(cfg.contextWindow.softPercent, 60);
  });
});

test("loadConfig lifts hardPercent to softPercent when it is configured lower", async () => {
  await withConfigFile({ contextWindow: { softPercent: 80, hardPercent: 60 } }, (cfg) => {
    assert.equal(cfg.contextWindow.softPercent, 80);
    assert.equal(cfg.contextWindow.hardPercent, 80);
  });
  // Clamping happens first, so a below-range hardPercent is lifted too.
  await withConfigFile({ contextWindow: { softPercent: 40, hardPercent: 1 } }, (cfg) => {
    assert.equal(cfg.contextWindow.hardPercent, 40);
  });
  await withConfigFile({ contextWindow: { softPercent: 40, hardPercent: 90 } }, (cfg) => {
    assert.equal(cfg.contextWindow.hardPercent, 90);
  });
});

test("loadConfig reads statusEveryTurn spellings from config.json", async () => {
  await withConfigFile({ contextWindow: { statusEveryTurn: "off" } }, (cfg) => {
    assert.equal(cfg.contextWindow.statusEveryTurn, false);
  });
  await withConfigFile({ contextWindow: { statusEveryTurn: "yes" } }, (cfg) => {
    assert.equal(cfg.contextWindow.statusEveryTurn, true);
  });
  await withConfigFile({ contextWindow: { statusEveryTurn: 0 } }, (cfg) => {
    assert.equal(cfg.contextWindow.statusEveryTurn, false);
  });
});

test("OPENVIKING_CONTEXT_STATUS_EVERY_TURN overrides config.json", async () => {
  await withConfigFile({ contextWindow: { statusEveryTurn: true } }, (cfg) => {
    assert.equal(cfg.contextWindow.statusEveryTurn, false);
  }, { OPENVIKING_CONTEXT_STATUS_EVERY_TURN: "0" });
  await withConfigFile({ contextWindow: { statusEveryTurn: false } }, (cfg) => {
    assert.equal(cfg.contextWindow.statusEveryTurn, true);
  }, { OPENVIKING_CONTEXT_STATUS_EVERY_TURN: "on" });
  // An unparseable value leaves the configured one alone.
  await withConfigFile({ contextWindow: { statusEveryTurn: false } }, (cfg) => {
    assert.equal(cfg.contextWindow.statusEveryTurn, false);
  }, { OPENVIKING_CONTEXT_STATUS_EVERY_TURN: "maybe" });
});

test("OPENVIKING_CONTEXT_RESET_DEADLINE_MS overrides config.json and is clamped", async () => {
  await withConfigFile({ contextWindow: { resetDeadlineMs: 60000 } }, (cfg) => {
    assert.equal(cfg.contextWindow.resetDeadlineMs, 120000);
  }, { OPENVIKING_CONTEXT_RESET_DEADLINE_MS: "120000" });
  await withConfigFile({ contextWindow: { resetDeadlineMs: 60000 } }, (cfg) => {
    assert.equal(cfg.contextWindow.resetDeadlineMs, 600000);
  }, { OPENVIKING_CONTEXT_RESET_DEADLINE_MS: "9999999" });
  await withConfigFile({ contextWindow: { resetDeadlineMs: 90000 } }, (cfg) => {
    assert.equal(cfg.contextWindow.resetDeadlineMs, 60000);
  }, { OPENVIKING_CONTEXT_RESET_DEADLINE_MS: "later" });
  // An empty value is not an override: the file value survives untouched.
  await withConfigFile({ contextWindow: { resetDeadlineMs: 90000 } }, (cfg) => {
    assert.equal(cfg.contextWindow.resetDeadlineMs, 90000);
  }, { OPENVIKING_CONTEXT_RESET_DEADLINE_MS: "" });
  // "0" is a truthy string, so it overrides and then clamps up to the floor —
  // there is no way to ask for a zero (non-blocking) reset deadline.
  await withConfigFile({ contextWindow: { resetDeadlineMs: 90000 } }, (cfg) => {
    assert.equal(cfg.contextWindow.resetDeadlineMs, 5000);
  }, { OPENVIKING_CONTEXT_RESET_DEADLINE_MS: "0" });
});

test("statusEveryTurn stays a boolean whatever the file and env say", async () => {
  // envBool's fallback is the raw configured value, so an unparseable env var
  // on top of a string in config.json must still normalize to a boolean.
  await withConfigFile({ contextWindow: { statusEveryTurn: "off" } }, (cfg) => {
    assert.equal(cfg.contextWindow.statusEveryTurn, false);
    assert.equal(typeof cfg.contextWindow.statusEveryTurn, "boolean");
  }, { OPENVIKING_CONTEXT_STATUS_EVERY_TURN: "maybe" });
  for (const value of ["banana", "", 2, []]) {
    await withConfigFile({ contextWindow: { statusEveryTurn: value } }, (cfg) => {
      assert.equal(typeof cfg.contextWindow.statusEveryTurn, "boolean", `statusEveryTurn: ${JSON.stringify(value)}`);
      assert.equal(cfg.contextWindow.statusEveryTurn, true, "unreadable spellings fall back to the default");
    });
  }
});
