import assert from "node:assert/strict";
import { readdir } from "node:fs/promises";
import test from "node:test";

import {
  claimForReplay,
  enqueue,
  listPending,
  replayPending,
} from "./lib/pending-queue.mjs";
import { withPendingDir } from "./testing/support.mjs";

test("replayPending sends queued entries and removes them after success", async () => {
  await withPendingDir(async () => {
    const payload = { role: "assistant", content: "queued response" };
    await enqueue("addMessage", "queue-replay", payload);

    const calls = [];
    const result = await replayPending(async (path, init) => {
      calls.push({ path, init });
      return { ok: true };
    }, () => {});

    assert.deepEqual(result, { replayed: 1, failed: 0, skipped: 0, deferred: 0 });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].path, "/api/v1/sessions/queue-replay/messages/batch");
    assert.deepEqual(JSON.parse(calls[0].init.body), { messages: [payload] });
    assert.deepEqual(await listPending(), []);
  });
});

test("replayPending batches consecutive same-session addMessage entries", async () => {
  await withPendingDir(async () => {
    const t0 = Date.now();
    await enqueue("addMessage", "queue-batch", { role: "user", content: "a" }, { createdAt: t0 });
    await enqueue("addMessage", "queue-batch", { role: "user", content: "b" }, { createdAt: t0 + 1 });
    await enqueue("addMessage", "queue-batch", { role: "user", content: "c" }, { createdAt: t0 + 2 });
    await enqueue("addMessage", "other", { role: "user", content: "x" }, { createdAt: t0 + 3 });

    const calls = [];
    const result = await replayPending(async (path, init) => {
      calls.push({ path, body: JSON.parse(init.body) });
      return { ok: true };
    }, () => {});

    assert.deepEqual(result, { replayed: 4, failed: 0, skipped: 0, deferred: 0 });
    assert.equal(calls.length, 2);
    assert.equal(calls[0].path, "/api/v1/sessions/queue-batch/messages/batch");
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
      await enqueue("addMessage", "queue-fail", { role: "user", content: `m${i}` }, { createdAt: t0 + i });
    }
    await enqueue("addMessage", "other-session", { role: "user", content: "later" }, { createdAt: t0 + 10 });

    const result = await replayPending(async () => ({ ok: false, status: 503 }), () => {});

    assert.equal(result.replayed, 0);
    assert.equal(result.failed, 3);
    assert.ok(result.deferred >= 1);
    const left = await listPending();
    assert.equal(left.length, 4);
    assert.equal(left.filter((p) => p.entry.sessionId === "queue-fail" && p.entry.retries === 1).length, 3);
    assert.equal(
      left.filter((p) => p.entry.sessionId === "other-session" && (p.entry.retries || 0) === 0).length,
      1,
    );
  });
});

test("enqueue deduplicates identical payloads", async () => {
  await withPendingDir(async () => {
    const payload = { role: "user", parts: [{ type: "text", text: "same" }] };
    const first = await enqueue("addMessage", "queue-dedup", payload);
    const second = await enqueue("addMessage", "queue-dedup", payload);

    assert.equal(first.ok, true);
    assert.equal(second.ok, true);
    assert.equal(second.deduped, true);
    assert.equal((await listPending()).length, 1);
  });
});

test("replayPending honors the per-run replay limit", async () => {
  await withPendingDir(async () => {
    process.env.OPENVIKING_PENDING_REPLAY_LIMIT = "1";
    await enqueue("addMessage", "queue-limit", { role: "user", content: "one" });
    await enqueue("addMessage", "queue-limit", { role: "user", content: "two" });

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

test("replayPending non-retryable batch failure isolates poison via serial fallback", async () => {
  await withPendingDir(async () => {
    const t0 = Date.now();
    await enqueue("addMessage", "queue-poison", { role: "user", content: "ok-a" }, { createdAt: t0 });
    await enqueue("addMessage", "queue-poison", { role: "user", content: "POISON" }, { createdAt: t0 + 1 });
    await enqueue("addMessage", "queue-poison", { role: "user", content: "ok-b" }, { createdAt: t0 + 2 });

    const calls = [];
    const result = await replayPending(async (path, init) => {
      const body = JSON.parse(init.body);
      calls.push({ path, body });
      if (path.endsWith("/messages/batch")) {
        return { ok: false, status: 422, error: { message: "invalid message in batch" } };
      }
      const content = body.content;
      if (content === "POISON") {
        return { ok: false, status: 422, error: { message: "schema" } };
      }
      return { ok: true };
    }, () => {});

    assert.equal(result.replayed, 2);
    assert.equal(result.skipped, 1);
    assert.equal(result.failed, 0);
    assert.ok(calls.some((c) => c.path.endsWith("/messages/batch")));
    assert.equal(calls.filter((c) => /\/messages$/.test(c.path)).length, 3);
    assert.deepEqual(await listPending(), []);
  });
});

test("claimForReplay atomically claims a file only once", async () => {
  await withPendingDir(async (dir) => {
    await enqueue("commitSession", "queue-claim", {});
    const [{ filename }] = await listPending();

    const firstClaim = await claimForReplay(filename);
    const secondClaim = await claimForReplay(filename);

    assert.match(firstClaim, /\.processing$/);
    assert.equal(secondClaim, null);
    assert.deepEqual(await readdir(dir), [firstClaim]);
  });
});
