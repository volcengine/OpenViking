import assert from "node:assert/strict";
import test from "node:test";

import { addMessage, commitSession, makeFetchJSON } from "./ov-session.mjs";
import { listPending, replayPending } from "./pending-queue.mjs";
import { withPendingDir } from "../../../memory-plugin-shared/testing/support.mjs";

test("addMessage queues retryable failures", async () => {
  await withPendingDir(async () => {
    const payload = { role: "user", content: "remember this" };
    const res = await addMessage(
      async () => ({ ok: false, status: 503, error: { message: "unavailable" } }),
      "cc-test-session",
      payload,
    );

    assert.equal(res.ok, false);
    assert.equal(res.pendingQueued, true);

    const pending = await listPending();
    assert.equal(pending.length, 1);
    assert.equal(pending[0].entry.type, "addMessage");
    assert.equal(pending[0].entry.sessionId, "cc-test-session");
    assert.deepEqual(pending[0].entry.payload, payload);
  });
});

test("addMessage does not queue non-retryable client failures", async () => {
  await withPendingDir(async () => {
    for (const status of [401, 403, 404, 409, 422]) {
      const res = await addMessage(
        async () => ({ ok: false, status, error: { message: `HTTP ${status}` } }),
        `cc-client-error-${status}`,
        { role: "user", content: `bad request ${status}` },
      );

      assert.equal(res.ok, false);
      assert.equal(res.pendingQueued, undefined);
      assert.equal(res.pendingEnqueueFailed, undefined);
    }

    assert.deepEqual(await listPending(), []);
  });
});

test("addMessage queues conflicts only when the server marks them retryable", async () => {
  await withPendingDir(async () => {
    const retryable = await addMessage(
      async () => ({
        ok: false,
        status: 409,
        error: {
          code: "CONFLICT",
          details: { conflict_type: "path_busy", retryable: true },
        },
      }),
      "cc-retryable-conflict",
      { role: "user", content: "retry after lock contention" },
    );
    assert.equal(retryable.pendingQueued, true);

    const terminal = await addMessage(
      async () => ({
        ok: false,
        status: 409,
        error: {
          code: "ALREADY_EXISTS",
          details: { retryable: false },
        },
      }),
      "cc-terminal-conflict",
      { role: "user", content: "do not retry business conflict" },
    );
    assert.equal(terminal.pendingQueued, undefined);
    assert.equal((await listPending()).length, 1);
  });
});

test("commitSession preserves retention payload across retry and replay", async () => {
  await withPendingDir(async () => {
    const payload = { keep_recent_count: 10 };
    const res = await commitSession(
      async () => ({
        ok: false,
        status: 409,
        error: {
          code: "CONFLICT",
          details: { conflict_type: "path_busy", retryable: true },
        },
      }),
      "cc-retryable-commit",
      payload,
    );

    assert.equal(res.pendingQueued, true);
    const pending = await listPending();
    assert.equal(pending.length, 1);
    assert.equal(pending[0].entry.type, "commitSession");
    assert.deepEqual(pending[0].entry.payload, payload);

    const calls = [];
    const logs = [];
    const result = await replayPending(async (path, init) => {
      calls.push({ path, init });
      return {
        ok: true,
        result: { status: "accepted", trace_id: "trace-replayed-commit" },
        traceId: "trace-replayed-commit",
      };
    }, (stage, data) => logs.push({ stage, data }));

    assert.deepEqual(result, { replayed: 1, failed: 0, skipped: 0, deferred: 0 });
    assert.equal(calls[0].path, "/api/v1/sessions/cc-retryable-commit/commit");
    assert.deepEqual(JSON.parse(calls[0].init.body), payload);
    assert.deepEqual(logs[1], {
      stage: "pending-queue",
      data: {
        action: "commit-replay",
        sessionId: "cc-retryable-commit",
        ok: true,
        status: "accepted",
        trace_id: "trace-replayed-commit",
        error: undefined,
      },
    });
  });
});

test("makeFetchJSON preserves commit trace_id on success and failure", async (t) => {
  const responses = [
    new Response(JSON.stringify({
      status: "ok",
      result: { status: "accepted", trace_id: "trace-success" },
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
    new Response(JSON.stringify({
      status: "error",
      error: { code: "INTERNAL", message: "commit failed", trace_id: "trace-error" },
    }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    }),
  ];
  t.mock.method(globalThis, "fetch", async () => responses.shift());
  const fetchJSON = makeFetchJSON({
    baseUrl: "http://127.0.0.1:1933",
    timeoutMs: 5000,
  });

  const success = await fetchJSON("/api/v1/sessions/trace-success/commit");
  assert.equal(success.traceId, "trace-success");
  assert.equal(success.result.trace_id, "trace-success");

  const failure = await fetchJSON("/api/v1/sessions/trace-error/commit");
  assert.equal(failure.ok, false);
  assert.equal(failure.traceId, "trace-error");
  assert.equal(failure.error.trace_id, "trace-error");
});
