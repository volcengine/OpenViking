import assert from "node:assert/strict";
import test from "node:test";

import {
  applyAgentSessionPolicy,
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

test("agent hook config defaults idle policy to one hour and supports off", () => {
  const previous = process.env.OPENVIKING_COMMIT_IDLE_TIMEOUT_SECONDS;
  try {
    delete process.env.OPENVIKING_COMMIT_IDLE_TIMEOUT_SECONDS;
    assert.equal(loadAgentHookConfig("cursor").commitIdleTimeoutSeconds, 3600);
    assert.equal(loadAgentHookConfig("cursor").commitTurnThreshold, 1);

    process.env.OPENVIKING_COMMIT_IDLE_TIMEOUT_SECONDS = "off";
    assert.equal(loadAgentHookConfig("cursor").commitIdleTimeoutSeconds, 0);
  } finally {
    if (previous === undefined) delete process.env.OPENVIKING_COMMIT_IDLE_TIMEOUT_SECONDS;
    else process.env.OPENVIKING_COMMIT_IDLE_TIMEOUT_SECONDS = previous;
  }
});

test("applyAgentSessionPolicy makes no request when disabled", async () => {
  let called = false;
  const result = await applyAgentSessionPolicy(
    async () => {
      called = true;
      return { ok: true, result: {} };
    },
    { commitIdleTimeoutSeconds: 0 },
    "cu-disabled",
  );
  assert.equal(called, false);
  assert.equal(result.method, "disabled");
});
