import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { addMessage, commitSession, makeFetchJSON } from "./ov-session.mjs";
import {
  claimForReplay,
  enqueue,
  listPending,
  replayPending,
} from "./pending-queue.mjs";

const originalEnv = {
  dir: process.env.OPENVIKING_PENDING_DIR,
  maxRetries: process.env.OPENVIKING_PENDING_MAX_RETRIES,
  replayLimit: process.env.OPENVIKING_PENDING_REPLAY_LIMIT,
  ttlDays: process.env.OPENVIKING_PENDING_TTL_DAYS,
};

async function withPendingDir(fn) {
  const dir = await mkdtemp(join(tmpdir(), "openviking-pending-test-"));
  process.env.OPENVIKING_PENDING_DIR = dir;
  delete process.env.OPENVIKING_PENDING_MAX_RETRIES;
  delete process.env.OPENVIKING_PENDING_REPLAY_LIMIT;
  delete process.env.OPENVIKING_PENDING_TTL_DAYS;
  try {
    return await fn(dir);
  } finally {
    if (originalEnv.dir === undefined) delete process.env.OPENVIKING_PENDING_DIR;
    else process.env.OPENVIKING_PENDING_DIR = originalEnv.dir;
    if (originalEnv.maxRetries === undefined) delete process.env.OPENVIKING_PENDING_MAX_RETRIES;
    else process.env.OPENVIKING_PENDING_MAX_RETRIES = originalEnv.maxRetries;
    if (originalEnv.replayLimit === undefined) delete process.env.OPENVIKING_PENDING_REPLAY_LIMIT;
    else process.env.OPENVIKING_PENDING_REPLAY_LIMIT = originalEnv.replayLimit;
    if (originalEnv.ttlDays === undefined) delete process.env.OPENVIKING_PENDING_TTL_DAYS;
    else process.env.OPENVIKING_PENDING_TTL_DAYS = originalEnv.ttlDays;
    await rm(dir, { recursive: true, force: true });
  }
}

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

test("replayPending sends queued entries and removes them after success", async () => {
  await withPendingDir(async () => {
    const payload = { role: "assistant", content: "queued response" };
    await enqueue("addMessage", "cc-replay", payload);

    const calls = [];
    const result = await replayPending(async (path, init) => {
      calls.push({ path, init });
      return { ok: true };
    }, () => {});

    assert.deepEqual(result, { replayed: 1, failed: 0, skipped: 0, deferred: 0 });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].path, "/api/v1/sessions/cc-replay/messages/batch");
    assert.deepEqual(JSON.parse(calls[0].init.body), { messages: [payload] });
    assert.deepEqual(await listPending(), []);
  });
});

test("replayPending batches consecutive same-session addMessage entries", async () => {
  await withPendingDir(async () => {
    const t0 = Date.now();
    await enqueue("addMessage", "cc-batch", { role: "user", content: "a" }, { createdAt: t0 });
    await enqueue("addMessage", "cc-batch", { role: "user", content: "b" }, { createdAt: t0 + 1 });
    await enqueue("addMessage", "cc-batch", { role: "user", content: "c" }, { createdAt: t0 + 2 });
    await enqueue("addMessage", "other", { role: "user", content: "x" }, { createdAt: t0 + 3 });

    const calls = [];
    const result = await replayPending(async (path, init) => {
      calls.push({ path, body: JSON.parse(init.body) });
      return { ok: true };
    }, () => {});

    assert.deepEqual(result, { replayed: 4, failed: 0, skipped: 0, deferred: 0 });
    assert.equal(calls.length, 2);
    assert.equal(calls[0].path, "/api/v1/sessions/cc-batch/messages/batch");
    assert.deepEqual(
      calls[0].body.messages.map((m) => m.content),
      ["a", "b", "c"],
    );
    assert.equal(calls[1].path, "/api/v1/sessions/other/messages/batch");
    assert.deepEqual(await listPending(), []);
  });
});

test("replayPending failed batch increments retries and stops for order", async () => {
  await withPendingDir(async () => {
    const t0 = Date.now();
    for (let i = 0; i < 3; i++) {
      await enqueue("addMessage", "cc-fail", { role: "user", content: `m${i}` }, { createdAt: t0 + i });
    }
    await enqueue("addMessage", "other-session", { role: "user", content: "later" }, { createdAt: t0 + 10 });

    const result = await replayPending(async () => ({ ok: false, status: 503 }), () => {});

    assert.equal(result.replayed, 0);
    assert.equal(result.failed, 3);
    assert.ok(result.deferred >= 1);
    const left = await listPending();
    assert.equal(left.length, 4);
    assert.equal(left.filter((p) => p.entry.sessionId === "cc-fail" && p.entry.retries === 1).length, 3);
    assert.equal(left.filter((p) => p.entry.sessionId === "other-session" && (p.entry.retries || 0) === 0).length, 1);
  });
});

test("enqueue deduplicates identical payloads", async () => {
  await withPendingDir(async () => {
    const payload = { role: "user", parts: [{ type: "text", text: "same" }] };
    const first = await enqueue("addMessage", "cc-dedup", payload);
    const second = await enqueue("addMessage", "cc-dedup", payload);

    assert.equal(first.ok, true);
    assert.equal(second.ok, true);
    assert.equal(second.deduped, true);
    assert.equal((await listPending()).length, 1);
  });
});

test("replayPending honors the per-run replay limit", async () => {
  await withPendingDir(async () => {
    process.env.OPENVIKING_PENDING_REPLAY_LIMIT = "1";
    await enqueue("addMessage", "cc-limit", { role: "user", content: "one" });
    await enqueue("addMessage", "cc-limit", { role: "user", content: "two" });

    const calls = [];
    const result = await replayPending(async (path, init) => {
      calls.push({ path, init });
      return { ok: true };
    }, () => {});

    assert.equal(result.replayed, 1);
    assert.equal(result.deferred, 1);
    assert.equal(calls.length, 1);
    assert.match(calls[0].path, /\/messages\/batch$/);
    assert.equal((await listPending()).length, 1);
  });
});

test("claimForReplay atomically claims a file only once", async () => {
  await withPendingDir(async (dir) => {
    await enqueue("commitSession", "cc-claim", {});
    const [{ filename }] = await listPending();

    const firstClaim = await claimForReplay(filename);
    const secondClaim = await claimForReplay(filename);

    assert.match(firstClaim, /\.processing$/);
    assert.equal(secondClaim, null);
    assert.deepEqual(await readdir(dir), [firstClaim]);
  });
});
