import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  enqueue,
  listPending,
  replayPending,
} from "./shared/pending-queue.mjs";

const originalEnv = {
  dir: process.env.OPENVIKING_PENDING_DIR,
  maxRetries: process.env.OPENVIKING_PENDING_MAX_RETRIES,
  replayLimit: process.env.OPENVIKING_PENDING_REPLAY_LIMIT,
  ttlDays: process.env.OPENVIKING_PENDING_TTL_DAYS,
};

async function withPendingDir(fn) {
  const dir = await mkdtemp(join(tmpdir(), "dsh-pending-test-"));
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

test("replay with consumeRetries:false keeps a retryable failure intact for the next attempt", async () => {
  await withPendingDir(async () => {
    const payload = { role: "user", content: "remember this" };
    await enqueue("addMessage", "dsh-drain", payload);

    let calls = 0;
    const first = await replayPending(
      async () => {
        calls += 1;
        return { ok: false, status: 503, error: { message: "unavailable" } };
      },
      () => {},
      { consumeRetries: false },
    );

    assert.equal(first.replayed, 0);
    assert.equal(first.failed, 1);
    assert.equal(calls, 1);

    const pending = await listPending();
    assert.equal(pending.length, 1);
    assert.equal(pending[0].entry.retries, 0);
    assert.ok(pending[0].filename.endsWith("_0.json"), pending[0].filename);
    assert.deepEqual(pending[0].entry.payload, payload);

    const recovered = await replayPending(
      async () => ({ ok: true }),
      () => {},
      { consumeRetries: false },
    );
    assert.equal(recovered.replayed, 1);
    assert.deepEqual(await listPending(), []);
  });
});

test("replay with consumeRetries:false still deletes exhausted entries", async () => {
  await withPendingDir(async () => {
    process.env.OPENVIKING_PENDING_MAX_RETRIES = "0";
    await enqueue("addMessage", "dsh-exhausted", { content: "doomed" });

    const result = await replayPending(
      async () => ({ ok: true }),
      () => {},
      { consumeRetries: false },
    );

    assert.equal(result.replayed, 0);
    assert.equal(result.skipped, 1);
    assert.deepEqual(await listPending(), []);
  });
});

test("replay with consumeRetries:false still deletes non-retryable failures", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "dsh-rejected", { content: "too large" });

    const result = await replayPending(
      async () => ({ ok: false, status: 413, error: { message: "too large" } }),
      () => {},
      { consumeRetries: false },
    );

    assert.equal(result.replayed, 0);
    assert.equal(result.skipped, 1);
    assert.deepEqual(await listPending(), []);
  });
});

test("replay with consumeRetries:false stops at the first retryable failure and preserves order", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "dsh-order", { content: "first" });
    await enqueue("addMessage", "dsh-order", { content: "second" });

    let calls = 0;
    const result = await replayPending(
      async () => {
        calls += 1;
        return { ok: false, status: 503, error: { message: "unavailable" } };
      },
      () => {},
      { consumeRetries: false },
    );

    assert.equal(calls, 1);
    assert.equal(result.failed, 1);
    const pending = await listPending();
    assert.deepEqual(
      pending.map(item => item.entry.payload.content),
      ["first", "second"],
    );
    assert.equal(pending[0].entry.retries, 0);
  });
});

test("replay with consumeRetries:false keeps going after a failed commitSession", async () => {
  await withPendingDir(async () => {
    await enqueue("commitSession", "dsh-commit", { keep_recent_count: 10 });
    await enqueue("addMessage", "dsh-commit", { content: "after the commit" });

    let calls = 0;
    const result = await replayPending(
      async (_path, init) => {
        calls += 1;
        if (init?.body && init.body.includes("keep_recent_count")) {
          return { ok: false, status: 503, error: { message: "unavailable" } };
        }
        return { ok: true };
      },
      () => {},
      { consumeRetries: false },
    );

    assert.equal(calls, 2);
    assert.equal(result.failed, 1);
    assert.equal(result.replayed, 1);
    const pending = await listPending();
    assert.deepEqual(
      pending.map(item => item.entry.type),
      ["commitSession"],
    );
    assert.equal(pending[0].entry.retries, 0);
  });
});

test("replay still consumes retries by default", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "dsh-default", { content: "flaky" });

    const result = await replayPending(
      async () => ({ ok: false, status: 503, error: { message: "unavailable" } }),
      () => {},
    );

    assert.equal(result.failed, 1);
    const pending = await listPending();
    assert.equal(pending.length, 1);
    assert.equal(pending[0].entry.retries, 1);
    assert.ok(pending[0].filename.endsWith("_1.json"), pending[0].filename);
  });
});
