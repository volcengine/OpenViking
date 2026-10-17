/**
 * Pending-queue dedup (DESIGN.md §5.5): a payload queued by a failed stop and
 * delivered live by the next one must not be replayed from the queue again —
 * the server has no turn-id idempotency.
 */

import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { buildMessagePayloads, planCapture, sendPlannedCapture } from "./ov-session.mjs";
import { enqueue, listPending, replayPending } from "./shared/pending-queue.mjs";

const turn = (id, text) => ({
  turnId: String(id),
  role: "user",
  text,
  parts: [{ type: "text", text }],
});

async function withQueue(fn) {
  const dir = await mkdtemp(join(tmpdir(), "ov-pending-test-"));
  const previous = process.env.OPENVIKING_PENDING_DIR;
  process.env.OPENVIKING_PENDING_DIR = dir;
  try {
    await fn();
  } finally {
    if (previous === undefined) delete process.env.OPENVIKING_PENDING_DIR;
    else process.env.OPENVIKING_PENDING_DIR = previous;
    await rm(dir, { recursive: true, force: true });
  }
}

test("a live send removes the queued copy it just delivered", async () => {
  await withQueue(async () => {
    const plan = planCapture([turn(1, "queued earlier")], new Set());
    const payload = buildMessagePayloads([turn(1, "queued earlier")])[0];
    assert.equal((await enqueue("addMessage", "kc-dedup", payload)).ok, true);
    assert.equal((await listPending()).length, 1);

    const result = await sendPlannedCapture(
      async () => ({ ok: true, status: 200 }),
      "kc-dedup",
      plan,
      {},
    );
    assert.equal(result.sent, 1);
    assert.equal((await listPending()).length, 0);
  });
});

test("a failed send keeps the queued copy", async () => {
  await withQueue(async () => {
    const plan = planCapture([turn(2, "still offline")], new Set());
    const payload = buildMessagePayloads([turn(2, "still offline")])[0];
    await enqueue("addMessage", "kc-dedup", payload);

    const result = await sendPlannedCapture(
      async () => ({ ok: false, status: 0, error: "ECONNREFUSED" }),
      "kc-dedup",
      plan,
      {},
    );
    assert.equal(result.queued, 1);
    assert.equal((await listPending()).length, 1);
  });
});

test("a queued copy for another session is untouched", async () => {
  await withQueue(async () => {
    const other = buildMessagePayloads([turn(3, "other session")])[0];
    await enqueue("addMessage", "kc-other", other);

    const plan = planCapture([turn(4, "this session")], new Set());
    await sendPlannedCapture(async () => ({ ok: true, status: 200 }), "kc-dedup", plan, {});

    const pending = await listPending();
    assert.equal(pending.length, 1);
    assert.equal(pending[0].entry.sessionId, "kc-other");
  });
});

test("a queued session-end commit replays after its messages", async () => {
  await withQueue(async () => {
    const first = buildMessagePayloads([turn(5, "one")])[0];
    const second = buildMessagePayloads([turn(6, "two")])[0];
    await enqueue("addMessage", "kc-order", first, { createdAt: 10 });
    await enqueue("addMessage", "kc-order", second, { createdAt: 11 });
    await enqueue("commitSession", "kc-order", {}, { createdAt: 12 });

    const calls = [];
    const fetchJSON = async (path, init) => {
      calls.push(`${init?.method || "GET"} ${path}`);
      return { ok: true, status: 200 };
    };
    const result = await replayPending(fetchJSON, () => {}, {});
    assert.equal(result.replayed, 3);
    assert.match(calls[0], /\/messages$/);
    assert.match(calls[1], /\/messages$/);
    assert.match(calls[2], /\/commit$/);
    assert.equal((await listPending()).length, 0);
  });
});
