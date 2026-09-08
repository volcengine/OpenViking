import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { filterCaptureTurns } from "./lib/capture-utils.mjs";

import {
  commitAgentSession,
  loadAgentHookConfig,
  makeAgentFetchJSON,
  resolveNativeSessionId,
  runHookStage,
} from "./lib/agent-hook-runtime.mjs";

function jsonResponse(status, value) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("agent fetch and commit logging preserve response trace_id", async (t) => {
  const responses = [
    jsonResponse(200, {
      status: "ok",
      result: {
        session_id: "agent-trace-success",
        status: "accepted",
        trace_id: "trace-agent-success",
      },
    }),
    jsonResponse(400, {
      status: "error",
      error: {
        code: "INTERNAL",
        message: "commit failed",
        trace_id: "trace-agent-error",
      },
    }),
  ];
  t.mock.method(globalThis, "fetch", async () => responses.shift());
  const { fetchJSON } = makeAgentFetchJSON({
    baseUrl: "http://127.0.0.1:1933",
    timeoutMs: 5000,
  });
  const logs = [];

  const success = await commitAgentSession(
    fetchJSON,
    "agent-trace-success",
    (stage, data) => logs.push({ stage, data }),
  );
  assert.equal(success.traceId, "trace-agent-success");
  assert.equal(success.result.trace_id, "trace-agent-success");
  assert.deepEqual(logs[0], {
    stage: "commit",
    data: {
      sessionId: "agent-trace-success",
      ok: true,
      status: "accepted",
      trace_id: "trace-agent-success",
      queued: false,
      error: undefined,
    },
  });

  const failure = await commitAgentSession(
    fetchJSON,
    "agent-trace-error",
    (stage, data) => logs.push({ stage, data }),
  );
  assert.equal(failure.ok, false);
  assert.equal(failure.traceId, "trace-agent-error");
  assert.equal(failure.error.trace_id, "trace-agent-error");
  assert.deepEqual(logs[1], {
    stage: "commit",
    data: {
      sessionId: "agent-trace-error",
      ok: false,
      status: 400,
      trace_id: "trace-agent-error",
      queued: false,
      error: "commit failed",
    },
  });
});

/**
 * Reporting a write no retry can fix used to be Claude Code's alone; the
 * harnesses on this runtime inherited it along with the write path, so what
 * they say and when they say it is pinned here rather than in one host.
 */
test("a failed write is parked when a retry can help and reported once when it cannot", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "ov-agent-hook-pending-"));
  const savedPendingDir = process.env.OPENVIKING_PENDING_DIR;
  const warnings = [];
  try {
    process.env.OPENVIKING_PENDING_DIR = join(dir, "pending");
    const responses = [
      jsonResponse(503, { status: "error", error: { code: "UNAVAILABLE", message: "restarting" } }),
      jsonResponse(400, { status: "error", error: { code: "INVALID", message: "bad payload" } }),
    ];
    t.mock.method(globalThis, "fetch", async () => responses.shift());
    t.mock.method(process.stderr, "write", (chunk) => {
      warnings.push(String(chunk));
      return true;
    });
    const { fetchJSON } = makeAgentFetchJSON({
      baseUrl: "http://127.0.0.1:1933",
      timeoutMs: 5000,
    });

    const retryable = await commitAgentSession(fetchJSON, "agent-pending-retryable");
    assert.equal(retryable.pendingQueued, true, "a retryable failure is parked for replay");
    assert.deepEqual(warnings, [], "a parked write is not something to warn about");

    const fatal = await commitAgentSession(fetchJSON, "agent-pending-fatal");
    assert.equal(fatal.pendingQueued, undefined);
    assert.equal(fatal.pendingEnqueueFailed, undefined);
    assert.deepEqual(warnings, [
      "[ov] commitSession failed with non-retryable status 400;"
        + " not enqueuing pending retry (bad payload)\n",
    ]);
  } finally {
    if (savedPendingDir === undefined) delete process.env.OPENVIKING_PENDING_DIR;
    else process.env.OPENVIKING_PENDING_DIR = savedPendingDir;
    rmSync(dir, { recursive: true, force: true });
  }
});

// The thin harnesses composed on this runtime used to send whatever their
// transcript parser produced. Every other harness runs the same filter, so it
// belongs beside the runtime rather than reimplemented in each hook.
test("the shared capture filter drops the turns no harness wants to remember", () => {
  const { kept, dropped } = filterCaptureTurns([
    { role: "user", content: "/compact" },
    { role: "assistant", content: "ok" },
    { role: "user", content: "..." },
    { role: "assistant", content: "[openviking-memory] recalled 3 items" },
    { role: "user", content: "the retry budget is three attempts" },
  ], { captureMaxLength: 24000 });

  assert.deepEqual(kept.map((turn) => turn.content), ["the retry budget is three attempts"]);
  assert.deepEqual(dropped.map((turn) => turn.reason), [
    "slash_command", "ack", "punctuation", "plugin_status",
  ]);
});

test("a turn past captureMaxLength is capped rather than dropped", () => {
  const long = "remember this detail. ".repeat(200);
  const { kept, dropped } = filterCaptureTurns(
    [{ role: "user", content: long }],
    { captureMaxLength: 200 },
  );

  assert.equal(dropped.length, 0);
  assert.ok(kept[0].content.length < long.length, "the turn must reach the extractor capped");
  assert.ok(kept[0].content.startsWith("remember this detail."));
});

test("filterCaptureTurns tolerates a missing or malformed turn list", () => {
  for (const input of [undefined, null, "text", 3, {}]) {
    assert.deepEqual(filterCaptureTurns(input), { kept: [], dropped: [] });
  }
});

/**
 * cursor, trae, trae-cn and zcode read the environment and nothing else before
 * this: an `ovcli.conf` `plugin` entry named after them was inert, and a
 * workspace file could not reach them at all. Assert the whole stack lands,
 * because none of these four has a config test of its own.
 */
test("the thin harnesses resolve the same layers as everyone else", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-agent-hook-config-"));
  const workspace = join(dir, "workspace");
  const saved = {
    cli: process.env.OPENVIKING_CLI_CONFIG_FILE,
    home: process.env.OPENVIKING_HOME,
    limit: process.env.OPENVIKING_RECALL_LIMIT,
  };
  try {
    mkdirSync(join(workspace, ".openviking"), { recursive: true });
    mkdirSync(join(workspace, ".git"), { recursive: true });
    writeFileSync(join(dir, "ovcli.conf"), JSON.stringify({
      url: "http://127.0.0.1:1933",
      plugin: {
        recallLimit: 7,
        cursor: { captureMode: "keyword" },
        "trae-cn": { scoreThreshold: 0.8 },
        zcode: { autoRecall: false },
      },
    }));
    process.env.OPENVIKING_CLI_CONFIG_FILE = join(dir, "ovcli.conf");
    // Keeps the identity cache and the workspace registry out of the real home.
    process.env.OPENVIKING_HOME = join(dir, "home");
    delete process.env.OPENVIKING_RECALL_LIMIT;

    const cursor = loadAgentHookConfig("cursor", workspace);
    assert.equal(cursor.recallLimit, 7, "the shared plugin section applies");
    assert.equal(cursor.captureMode, "keyword", "the per-harness override applies");
    assert.equal(cursor.autoRecall, true, "another harness's override does not");
    assert.equal(loadAgentHookConfig("zcode", workspace).autoRecall, false);
    // The hyphenated spelling of a harness finds the same override.
    assert.equal(loadAgentHookConfig("trae-cn", workspace).scoreThreshold, 0.8);
    assert.equal(loadAgentHookConfig("trae", workspace).scoreThreshold, 0.35);

    // A workspace file outranks ovcli.conf, and the environment outranks both.
    writeFileSync(
      join(workspace, ".openviking", "config.json"),
      JSON.stringify({ version: 1, recall: { max_items: 4 }, capture: { enabled: false } }),
    );
    const pinned = loadAgentHookConfig("cursor", workspace);
    assert.equal(pinned.recallLimit, 4);
    assert.equal(pinned.autoCapture, false);

    process.env.OPENVIKING_RECALL_LIMIT = "2";
    assert.equal(loadAgentHookConfig("cursor", workspace).recallLimit, 2);

    // And a directory with neither file keeps the ovcli.conf answer.
    assert.equal(loadAgentHookConfig("cursor", dir).recallLimit, 2);
  } finally {
    for (const [key, value] of [
      ["OPENVIKING_CLI_CONFIG_FILE", saved.cli],
      ["OPENVIKING_HOME", saved.home],
      ["OPENVIKING_RECALL_LIMIT", saved.limit],
    ]) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    rmSync(dir, { recursive: true, force: true });
  }
});

/**
 * `plugin.<harness>.apiKey` was inert on these four: the credential chain never
 * reads the `plugin` section, so its empty answer used to overwrite the one the
 * settings layers had already found.
 */
test("an ovcli.conf plugin key reaches a thin harness when nothing else supplies one", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-agent-hook-key-"));
  const overrides = [
    "OPENVIKING_CLI_CONFIG_FILE",
    "OPENVIKING_CONFIG_FILE",
    "OPENVIKING_HOME",
    "OPENVIKING_API_KEY",
    "OPENVIKING_BEARER_TOKEN",
    "OPENVIKING_URL",
    "OPENVIKING_BASE_URL",
    "OPENVIKING_CREDENTIAL_SOURCE",
    "OPENVIKING_CREDENTIALS_SOURCE",
  ];
  const saved = Object.fromEntries(overrides.map((key) => [key, process.env[key]]));
  try {
    for (const key of overrides) delete process.env[key];
    writeFileSync(join(dir, "ovcli.conf"), JSON.stringify({
      url: "http://127.0.0.1:1933",
      plugin: { cursor: { apiKey: "sk-plugin-cursor" } },
    }));
    process.env.OPENVIKING_CLI_CONFIG_FILE = join(dir, "ovcli.conf");
    process.env.OPENVIKING_CONFIG_FILE = join(dir, "ov.conf");
    process.env.OPENVIKING_HOME = join(dir, "home");

    assert.equal(loadAgentHookConfig("cursor", dir).apiKey, "sk-plugin-cursor");
    assert.equal(loadAgentHookConfig("trae", dir).apiKey, "", "another harness's key stays its own");
  } finally {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    rmSync(dir, { recursive: true, force: true });
  }
});

/**
 * Every Claude Code and Codex hook entry opens the same way, and the copies
 * drifted: some re-read the config for the payload's directory, some gated
 * against the hook process's own. `runHookStage` is that opening.
 */
async function stageRun({ raw = "{}", cfg = {}, input = {}, ...rest } = {}, run = () => undefined) {
  const seen = { cwds: [], skips: [], skipStages: [], envelopes: [] };
  const value = await runHookStage({
    loadConfig: (cwd) => {
      seen.cwds.push(cwd);
      return { ...cfg, cwd };
    },
    input: { read: async () => raw, ...input },
    envelope: (envelope) => seen.envelopes.push(envelope),
    onSkip: (reason, stage) => { seen.skips.push(reason); seen.skipStages.push(stage); },
    ...rest,
  }, run);
  return { seen, value };
}

test("the hook stage reloads the config for the payload's directory", async () => {
  const stages = [];
  const { seen, value } = await stageRun(
    { raw: JSON.stringify({ session_id: "s1", cwd: "/w/one" }), cfg: { autoCapture: true } },
    async (stage) => { stages.push(stage); return "captured 1 turn"; },
  );

  assert.equal(value, "captured 1 turn");
  assert.deepEqual(seen.cwds, ["/w/one"]);
  assert.equal(stages[0].cfg.cwd, "/w/one");
  assert.equal(stages[0].sessionId, "s1");
  assert.equal(stages[0].bypassed, false);
  assert.deepEqual(seen.envelopes, ["captured 1 turn"], "the callback's value is the envelope");
});

test("a closed gate answers with an empty envelope and never runs the callback", async () => {
  const payload = JSON.stringify({ session_id: "s1", cwd: "/w" });
  const cases = [
    ["bad_stdin", { raw: "{not json" }],
    ["disabled", { raw: payload, gates: { enabled: (cfg) => cfg.autoRecall } }],
    ["bypass", { raw: payload, cfg: { bypassSession: true } }],
  ];
  for (const [reason, options] of cases) {
    let ran = false;
    const { seen, value } = await stageRun(options, () => { ran = true; });

    assert.equal(value, undefined);
    assert.equal(ran, false, `${reason} must not reach the callback`);
    assert.deepEqual(seen.skips, [reason]);
    assert.deepEqual(seen.envelopes, [undefined]);
    // Every skip hands back the same stage, so an entry that records one does
    // not have to know which gate closed to read the payload off it.
    assert.deepEqual(
      Object.keys(seen.skipStages[0]).sort(),
      ["bypassed", "cfg", "cwd", "emit", "input", "raw", "sessionId"],
      `${reason} passes the stage`,
    );
  }
});

test("a tolerant reader carries on with an empty payload", async () => {
  const { seen, value } = await stageRun(
    { raw: "{not json", input: { tolerant: true } },
    async ({ input }) => (Object.keys(input).length === 0 ? "empty" : "payload"),
  );

  assert.equal(value, "empty");
  assert.deepEqual(seen.skips, []);
});

test("a bypassed session still reaches a callback that turned the gate off", async () => {
  const stages = [];
  const { seen } = await stageRun(
    {
      raw: JSON.stringify({ session_id: "s1", cwd: "/w" }),
      cfg: { bypassSession: true },
      gates: { bypass: () => false },
    },
    async (stage) => { stages.push(stage); },
  );

  assert.equal(stages[0].bypassed, true, "the verdict is still on the stage");
  assert.deepEqual(seen.skips, []);
});

/**
 * The thin harnesses spell the session id four ways, so the gate that reads it
 * has to be given their resolver rather than switched off and rebuilt inside
 * the callback.
 */
test("a host resolver decides which session the bypass gate is looking at", async () => {
  const options = {
    raw: JSON.stringify({ conversation_id: "scratch-123", cwd: "/w" }),
    cfg: { bypassSessionPatterns: ["scratch-*"] },
  };

  const narrow = await stageRun(options, () => "ran");
  assert.equal(narrow.value, "ran", "the default lookup never sees conversation_id");

  const resolved = await stageRun({ ...options, sessionId: resolveNativeSessionId }, () => "ran");
  assert.equal(resolved.value, undefined);
  assert.deepEqual(resolved.seen.skips, ["bypass"]);
  assert.equal(resolved.seen.skipStages[0].sessionId, "scratch-123");
});

test("the envelope is written once when the callback answers early", async () => {
  const { seen } = await stageRun(
    { raw: JSON.stringify({ session_id: "s1", cwd: "/w" }) },
    async ({ emit }) => { emit("detached"); return "worker value"; },
  );

  assert.deepEqual(seen.envelopes, ["detached"]);
});
