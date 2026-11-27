import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { sendSessionMessages } from "./lib/batch-send.mjs";
import { enqueue, listPending } from "./lib/pending-queue.mjs";

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
    retryLater: 0,
    retryable: false,
    authFailed: false,
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

test("a refused batch is sent one by one, so only the refused message fails", async () => {
  await withPendingDir(async (dir) => {
    const order = [];
    const res = await sendSessionMessages(
      async (path, init) => {
        if (path.endsWith("/messages/batch")) return { ok: false, status: 422, error: { message: "invalid" } };
        return parseBody(init).content === "message-1"
          ? { ok: false, status: 422, error: { message: "invalid" } }
          : { ok: true, status: 200, result: {} };
      },
      "bad-session",
      payloads(4),
      {
        enqueueOnRetryable: true,
        onSent: (n) => order.push(["sent", n]),
        onRejected: (n) => order.push(["rejected", n]),
      },
    );

    assert.equal(res.sent, 3);
    assert.equal(res.failed, 1);
    assert.equal(res.retryLater, 0);
    assert.deepEqual(order, [["sent", 1], ["rejected", 1], ["sent", 1], ["sent", 1]]);
    assert.equal((await readdir(dir).catch(() => [])).filter((name) => name.endsWith(".json")).length, 0);
  });
});

test("refused credentials send and queue nothing more and leave the rest to the caller", async () => {
  await withPendingDir(async (dir) => {
    let calls = 0;
    const res = await sendSessionMessages(
      async () => {
        calls += 1;
        return calls === 1 ? { ok: true, status: 200, result: {} } : { ok: false, status: 401, error: { code: "UNAUTHENTICATED" } };
      },
      "revoked",
      payloads(150),
      { enqueueOnRetryable: true },
    );

    assert.equal(calls, 2);
    assert.equal(res.sent, 100);
    assert.equal(res.queued, 0);
    assert.equal(res.retryLater, 50);
    assert.equal(res.authFailed, true);
    assert.equal((await readdir(dir).catch(() => [])).filter((name) => name.endsWith(".json")).length, 0);
  });
});

test("new messages wait behind the session's queued ones", async () => {
  await withPendingDir(async () => {
    const fetchJSON = Object.assign(async () => ({ ok: false, status: 503 }), { origin: "o1" });
    await enqueue("addMessage", "ordered", { role: "user", content: "older" }, { createdAt: Date.now() + 60_000, origin: "o1" });
    await enqueue("addMessage", "ordered", { role: "user", content: "elsewhere" }, { origin: "o2" });

    const queued = await sendSessionMessages(fetchJSON, "ordered", payloads(2), { enqueueOnRetryable: true });
    assert.equal(queued.queued, 2);
    const mine = (await listPending()).filter(({ entry }) => entry.origin === "o1");
    assert.deepEqual(mine.map(({ entry }) => entry.payload.content), ["older", "message-0", "message-1"]);

    const held = await sendSessionMessages(fetchJSON, "ordered", payloads(2));
    assert.equal(held.sent + held.queued, 0);
    assert.equal(held.retryLater, 2);
  });
});

test("sendSessionMessages queues only explicitly retryable conflicts", async () => {
  await withPendingDir(async () => {
    const retryable = await sendSessionMessages(
      async () => ({
        ok: false,
        status: 409,
        error: {
          code: "CONFLICT",
          details: { conflict_type: "path_busy", retryable: true },
        },
      }),
      "retryable-conflict",
      payloads(2),
      { enqueueOnRetryable: true },
    );
    assert.equal(retryable.queued, 2);
    assert.equal(retryable.failed, 0);

    const terminal = await sendSessionMessages(
      async () => ({
        ok: false,
        status: 409,
        error: {
          code: "ALREADY_EXISTS",
          details: { retryable: false },
        },
      }),
      "terminal-conflict",
      payloads(2, 2),
      { enqueueOnRetryable: true },
    );
    assert.equal(terminal.queued, 0);
    assert.equal(terminal.failed, 2);
    assert.equal((await listPending()).length, 2);
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
  await withPendingDir(async () => {
    // The second payload serializes for the batch request but not for the
    // queue file, so enqueue reports ok:false for exactly that payload.
    let serialized = 0;
    const messages = payloads(3);
    messages[1] = { ...messages[1], toJSON() {
      if (++serialized > 1) throw new Error("not serializable");
      return { role: "user", content: "message-1" };
    } };

    const res = await sendSessionMessages(
      async () => ({ ok: false, status: 503, error: { message: "unavailable" } }),
      "prefix-session",
      messages,
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

test("a 404 on the one-message route holds the rest for the caller instead of refusing it", async () => {
  await withPendingDir(async () => {
    const rejected = [];
    const sent = [];
    const res = await sendSessionMessages(
      async (path, init) => {
        if (path.endsWith("/messages/batch")) return { ok: false, status: 404, error: { message: "missing" } };
        const { content } = parseBody(init);
        if (content === "message-0") {
          sent.push(content);
          return { ok: true, status: 200, result: {} };
        }
        return { ok: false, status: 404, error: { code: "NOT_FOUND" } };
      },
      "gone",
      payloads(3),
      { enqueueOnRetryable: true, onRejected: (n) => rejected.push(n) },
    );

    assert.deepEqual(sent, ["message-0"]);
    assert.equal(res.sent, 1);
    assert.equal(res.retryLater, 2);
    assert.equal(res.queued, 0);
    assert.deepEqual(rejected, []);
  });
});

test("without isolateRejected the serial fallback stops at the first refused message", async () => {
  await withPendingDir(async () => {
    const order = [];
    const res = await sendSessionMessages(
      async (path, init) => {
        if (path.endsWith("/messages/batch")) return { ok: false, status: 405, error: { message: "no batch route" } };
        const { content } = parseBody(init);
        order.push(content);
        return content === "message-1"
          ? { ok: false, status: 422, error: { message: "invalid" } }
          : { ok: true, status: 200, result: {} };
      },
      "strict",
      payloads(4),
      {
        isolateRejected: false,
        onSent: (n) => order.push(["sent", n]),
        onRejected: (n) => order.push(["rejected", n]),
      },
    );

    assert.deepEqual(order, ["message-0", ["sent", 1], "message-1", ["rejected", 3]]);
    assert.equal(res.sent, 1);
    assert.equal(res.failed, 3);
    assert.equal(res.retryLater, 0);
  });
});
