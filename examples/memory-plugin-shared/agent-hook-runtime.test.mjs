import assert from "node:assert/strict";
import test from "node:test";

import {
  commitAgentSession,
  loadAgentHookConfig,
  makeAgentFetchJSON,
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

test("blank numeric env vars fall back to defaults instead of clamping to the minimum", () => {
  // Shell rc files commonly export these as empty strings (`export OPENVIKING_TIMEOUT_MS=`).
  // envBool already treats a blank value as unset; envNumber must agree, otherwise
  // Number("") === 0 silently clamps every knob to its floor (recall limit 10 -> 1,
  // timeout 15s -> 1s) with no warning.
  const names = [
    "OPENVIKING_RECALL_LIMIT",
    "OPENVIKING_RECALL_TOKEN_BUDGET",
    "OPENVIKING_RECALL_MAX_CONTENT_CHARS",
    "OPENVIKING_SCORE_THRESHOLD",
    "OPENVIKING_TIMEOUT_MS",
    "OPENVIKING_PROFILE_TOKEN_BUDGET",
    "OPENVIKING_COMMIT_TURN_THRESHOLD",
  ];
  const saved = Object.fromEntries(names.map((name) => [name, process.env[name]]));
  for (const name of names) process.env[name] = "";
  try {
    const cfg = loadAgentHookConfig("test-client");
    assert.equal(cfg.recallLimit, 10);
    assert.equal(cfg.recallTokenBudget, 2000);
    assert.equal(cfg.recallMaxContentChars, 500);
    assert.equal(cfg.scoreThreshold, 0.35);
    assert.equal(cfg.timeoutMs, 15000);
    assert.equal(cfg.profileTokenBudget, 6000);
    assert.equal(cfg.commitTurnThreshold, 8);
    assert.equal(cfg.recallLimitConfigured, false);
  } finally {
    for (const [name, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
});

test("whitespace-only numeric env vars fall back to defaults too", () => {
  const saved = process.env.OPENVIKING_TIMEOUT_MS;
  process.env.OPENVIKING_TIMEOUT_MS = "   ";
  try {
    const cfg = loadAgentHookConfig("test-client");
    assert.equal(cfg.timeoutMs, 15000);
  } finally {
    if (saved === undefined) delete process.env.OPENVIKING_TIMEOUT_MS;
    else process.env.OPENVIKING_TIMEOUT_MS = saved;
  }
});
