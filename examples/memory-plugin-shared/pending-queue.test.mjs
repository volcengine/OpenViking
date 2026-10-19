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

    assert.deepEqual(
      {
        replayed: result.replayed,
        failed: result.failed,
        skipped: result.skipped,
        deferred: result.deferred,
        cleaned: result.cleaned,
      },
      { replayed: 1, failed: 0, skipped: 0, deferred: 0, cleaned: 0 },
    );
    assert.equal(result.results.length, 1);
    assert.equal(result.results[0].outcome, "sent");
    assert.equal(result.results[0].type, "addMessage");
    assert.equal(result.results[0].sessionId, "queue-replay");
    assert.equal(calls.length, 1);
    assert.equal(calls[0].path, "/api/v1/sessions/queue-replay/messages");
    assert.deepEqual(JSON.parse(calls[0].init.body), payload);
    assert.deepEqual(await listPending(), []);
  });
});

test("listPending hides claimed entries unless asked, then marks them", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "queue-claiming", { role: "user", content: "held" });
    const [{ filename }] = await listPending();
    const claimed = await claimForReplay(filename);

    // A replayer must not see the entry another process is sending right now.
    assert.deepEqual(await listPending(), []);

    const visible = await listPending({ includeProcessing: true });
    assert.equal(visible.length, 1);
    assert.equal(visible[0].filename, claimed);
    assert.equal(visible[0].processing, true);

    // The ordinary listing keeps its old shape: no flag on unclaimed entries.
    await enqueue("addMessage", "queue-claiming", { role: "user", content: "plain" });
    const [plain] = await listPending();
    assert.equal("processing" in plain, false);
  });
});

test("replayPending tells a rejection apart from a retryable failure", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "queue-outcome", { role: "user", content: "rejected" });
    await enqueue("addMessage", "queue-outcome", { role: "user", content: "retryable" });

    const result = await replayPending(async (path, init) => {
      const body = JSON.parse(init.body);
      return body.content === "rejected" ? { ok: false, status: 400 } : { ok: false, status: 503 };
    }, () => {});

    assert.deepEqual(
      result.results.map(({ type, sessionId, outcome }) => ({ type, sessionId, outcome })),
      [
        { type: "addMessage", sessionId: "queue-outcome", outcome: "rejected" },
        { type: "addMessage", sessionId: "queue-outcome", outcome: "failed" },
      ],
    );
    assert.equal(result.skipped, 1);
    assert.equal(result.failed, 1);
    assert.equal((await listPending()).length, 1);
  });
});

test("replayPending parks later addMessages behind one that failed", async () => {
  await withPendingDir(async () => {
    for (const content of ["first", "second", "third"]) {
      await enqueue("addMessage", "queue-order", { role: "user", content });
    }

    const result = await replayPending(async (path, init) => {
      const body = JSON.parse(init.body);
      return body.content === "first" ? { ok: false, status: 503 } : { ok: true };
    }, () => {});

    assert.deepEqual(result.results.map(({ outcome, reason }) => ({ outcome, reason })), [
      { outcome: "failed", reason: undefined },
      { outcome: "deferred", reason: "ordering" },
      { outcome: "deferred", reason: "ordering" },
    ]);
    assert.equal(result.deferred, 2);
    assert.equal((await listPending()).length, 3);
  });
});

test("replayPending only attaches the delivered payload when asked", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "queue-payload", { role: "user", content: "delivered" });

    const lean = await replayPending(async () => ({ ok: true }), () => {});
    assert.equal("payload" in lean.results[0], false);

    await enqueue("addMessage", "queue-payload", { role: "user", content: "delivered-again" });
    const rich = await replayPending(async () => ({ ok: true }), () => {}, { includePayload: true });
    assert.deepEqual(rich.results[0].payload, { role: "user", content: "delivered-again" });
  });
});

test("replayPending never sends an entry another process has claimed", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "queue-held", { role: "user", content: "claimed elsewhere" });
    const [{ filename }] = await listPending();
    await claimForReplay(filename);

    const calls = [];
    const result = await replayPending(async (path, init) => {
      calls.push({ path, init });
      return { ok: true };
    }, () => {});

    assert.equal(calls.length, 0);
    assert.deepEqual(result.results, []);
    assert.equal(result.replayed, 0);
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
    assert.equal((await listPending()).length, 1);
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
