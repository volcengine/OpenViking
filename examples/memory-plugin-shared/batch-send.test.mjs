import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { sendSessionMessages } from "./lib/batch-send.mjs";
import { listPending } from "./lib/pending-queue.mjs";

function payloads(count, offset = 0) {
  return Array.from({ length: count }, (_, i) => ({
    role: "user",
    content: `message-${offset + i}`,
  }));
}

function parseBody(init) {
  return JSON.parse(init?.body || "{}");
}

async function withPendingDir(fn) {
  const old = process.env.OPENVIKING_PENDING_DIR;
  const dir = await mkdtemp(join(tmpdir(), "ov-batch-send-"));
  process.env.OPENVIKING_PENDING_DIR = dir;
  try {
    return await fn(dir);
  } finally {
    if (old == null) delete process.env.OPENVIKING_PENDING_DIR;
    else process.env.OPENVIKING_PENDING_DIR = old;
    await rm(dir, { recursive: true, force: true });
  }
}

test("sendSessionMessages chunks batches at the server limit", async () => {
  const chunkSizes = [];
  const onSent = [];
  const res = await sendSessionMessages(
    async (path, init) => {
      assert.equal(path, "/api/v1/sessions/session-1/messages/batch");
      const body = parseBody(init);
      chunkSizes.push(body.messages.length);
      return { ok: true, status: 200, result: { added: -1 } };
    },
    "session-1",
    payloads(250),
    { onSent: async (n) => onSent.push(n) },
  );

  assert.deepEqual(chunkSizes, [100, 100, 50]);
  assert.deepEqual(onSent, [100, 100, 50]);
  assert.deepEqual(res, {
    sent: 250,
    queued: 0,
    enqueueFailed: 0,
    failed: 0,
    retryable: false,
    usedBatch: true,
    lastError: null,
  });
});

for (const status of [404, 405]) {
  test(`sendSessionMessages falls back to serial sends on batch ${status}`, async () => {
    const calls = [];
    const sentBodies = [];
    const res = await sendSessionMessages(
      async (path, init) => {
        calls.push(path);
        if (path.endsWith("/messages/batch")) return { ok: false, status, error: { message: "missing" } };
        sentBodies.push(parseBody(init));
        return { ok: true, status: 200, result: { ok: true } };
      },
      "legacy-server",
      payloads(3),
    );

    assert.deepEqual(calls, [
      "/api/v1/sessions/legacy-server/messages/batch",
      "/api/v1/sessions/legacy-server/messages",
      "/api/v1/sessions/legacy-server/messages",
      "/api/v1/sessions/legacy-server/messages",
    ]);
    assert.deepEqual(sentBodies.map((body) => body.content), ["message-0", "message-1", "message-2"]);
    assert.equal(res.sent, 3);
    assert.equal(res.usedBatch, false);
    assert.equal(res.failed, 0);
  });
}

test("sendSessionMessages queues the unsent suffix after a retryable batch failure", async () => {
  await withPendingDir(async (dir) => {
    let batch = 0;
    const res = await sendSessionMessages(
      async () => {
        batch += 1;
        if (batch === 1) return { ok: true, status: 200, result: { ok: true } };
        return { ok: false, status: 503, error: { message: "unavailable" } };
      },
      "retry-session",
      payloads(250),
      { enqueueOnRetryable: true },
    );

    assert.equal(res.sent, 100);
    assert.equal(res.queued, 150);
    assert.equal(res.failed, 0);
    assert.equal(res.retryable, true);
    assert.equal((await readdir(dir)).filter((name) => name.endsWith(".json")).length, 150);
    const pending = await listPending();
    assert.deepEqual(
      pending.slice(0, 3).map(({ entry }) => entry.payload.content),
      ["message-100", "message-101", "message-102"],
    );
    assert.ok(pending[1].entry.createdAt > pending[0].entry.createdAt);
  });
});

test("sendSessionMessages treats non-retryable failures as failed without enqueueing", async () => {
  await withPendingDir(async (dir) => {
    const res = await sendSessionMessages(
      async () => ({ ok: false, status: 400, error: { message: "bad request" } }),
      "bad-session",
      payloads(4),
      { enqueueOnRetryable: true },
    );

    assert.equal(res.sent, 0);
    assert.equal(res.queued, 0);
    assert.equal(res.failed, 4);
    assert.equal(res.retryable, false);
    assert.equal((await readdir(dir).catch(() => [])).filter((name) => name.endsWith(".json")).length, 0);
  });
});

test("sendSessionMessages treats missing status failures as retryable", async () => {
  await withPendingDir(async () => {
    const res = await sendSessionMessages(
      async () => ({ ok: false, error: { message: "network-ish" } }),
      "unknown-status",
      payloads(2),
      { enqueueOnRetryable: true },
    );

    assert.equal(res.sent, 0);
    assert.equal(res.queued, 2);
    assert.equal(res.failed, 0);
    assert.equal(res.retryable, true);
  });
});

test("sendSessionMessages stops queueing at the first enqueue failure to keep the queued prefix contiguous", async () => {
  await withPendingDir(async (dir) => {
    // Poison the second payload's pending filename: the pre-created garbage
    // file triggers EEXIST on write and defeats the dedup-recovery read, so
    // enqueue reports ok:false for exactly that payload. Filename layout
    // mirrors pending-queue.mjs makeDedupKey/pendingFilename.
    const dedupKey = createHash("sha256")
      .update("addMessage")
      .update("\n")
      .update("prefix-session")
      .update("\n")
      .update('{"content":"message-1","role":"user"}')
      .digest("hex");
    await writeFile(join(dir, `${dedupKey}_0.json`), "not json", "utf-8");

    const res = await sendSessionMessages(
      async () => ({ ok: false, status: 503, error: { message: "unavailable" } }),
      "prefix-session",
      payloads(3),
      { enqueueOnRetryable: true },
    );

    // message-0 queued, message-1 failed to enqueue, message-2 must NOT be
    // queued: consumers mark the first sent+queued payloads as captured, so a
    // queued entry after a gap would let the gapped message be dropped.
    assert.equal(res.sent, 0);
    assert.equal(res.queued, 1);
    assert.equal(res.enqueueFailed, 2);
    assert.equal(res.failed, 0);
    assert.equal(res.retryable, true);
    const pending = await listPending();
    assert.deepEqual(
      pending.map(({ entry }) => entry.payload.content),
      ["message-0"],
    );
  });
});

test("sendSessionMessages queues the remaining suffix after serial fallback failure", async () => {
  await withPendingDir(async () => {
    let serialCount = 0;
    const onSent = [];
    const res = await sendSessionMessages(
      async (path) => {
        if (path.endsWith("/messages/batch")) return { ok: false, status: 404, error: { message: "missing" } };
        serialCount += 1;
        if (serialCount <= 2) return { ok: true, status: 200, result: { ok: true } };
        return { ok: false, status: 503, error: { message: "unavailable" } };
      },
      "serial-retry",
      payloads(5),
      { enqueueOnRetryable: true, onSent: async (n) => onSent.push(n) },
    );

    assert.equal(res.usedBatch, false);
    assert.equal(res.sent, 2);
    assert.equal(res.queued, 3);
    assert.equal(res.failed, 0);
    assert.deepEqual(onSent, [1, 1]);
  });
});
