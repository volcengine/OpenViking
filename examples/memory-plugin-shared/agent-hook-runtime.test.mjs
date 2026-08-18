import assert from "node:assert/strict";
import test from "node:test";

import {
  commitAgentSession,
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
