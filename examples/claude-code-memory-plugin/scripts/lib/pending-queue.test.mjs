import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { addMessage } from "./ov-session.mjs";
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
    for (const status of [401, 403, 404, 422]) {
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
    assert.equal(calls[0].path, "/api/v1/sessions/cc-replay/messages");
    assert.deepEqual(JSON.parse(calls[0].init.body), payload);
    assert.deepEqual(await listPending(), []);
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
