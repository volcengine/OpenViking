/**
 * The replay contract after the second review.
 *
 * Each test here names a behaviour the first implementation got wrong, and the
 * assertion fails against that version: a global stop that blocked unrelated
 * sessions, a predicate that ran after the TTL and retry checks, and deletions
 * that reported success when the file was still there.
 */

import assert from "node:assert/strict";
import { readdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";

import {
  cleanStale,
  enqueue,
  listPending,
  replayPending,
  replayPendingDetailed,
} from "./lib/pending-queue.mjs";
import { withPendingDir } from "./testing/support.mjs";

const ok = async () => ({ ok: true, status: 200, result: {} });

test("a stuck session does not block another session's queue", async () => {
  await withPendingDir(async () => {
    // `listPending` sorts by `createdAt`; three entries in the same millisecond
    // would fall back to directory order.
    const t0 = Date.now();
    await enqueue("addMessage", "ov-a", { content: "a-first" }, { createdAt: t0 });
    await enqueue("addMessage", "ov-a", { content: "a-second" }, { createdAt: t0 + 1 });
    await enqueue("addMessage", "ov-b", { content: "b-only" }, { createdAt: t0 + 2 });

    const result = await replayPendingDetailed(
      async (path, init) => {
        const body = JSON.parse(init.body);
        if (body.content === "a-first") return { ok: false, status: 503, error: { message: "down" } };
        return ok();
      },
      () => {},
    );

    // ov-a stops after its first failure, ov-b is untouched by it.
    assert.deepEqual(
      result.results.map((item) => [item.sessionId, item.status]),
      [["ov-a", "failed"], ["ov-a", "deferred"], ["ov-b", "sent"]],
    );
    assert.equal(result.replayed, 1);
    assert.equal(result.deferred, 1);
    assert.equal((await listPending()).length, 2);
  });
});

test("a predicate runs before the TTL check, so a deferred item never expires", async () => {
  await withPendingDir(async (dir) => {
    process.env.OPENVIKING_PENDING_TTL_DAYS = "1";
    const queued = await enqueue("commitSession", "ov-held", { keep_recent_count: 0 });

    // Age it past the TTL without touching the file's mtime.
    const file = join(dir, queued.path);
    const entry = JSON.parse(await (await import("node:fs/promises")).readFile(file, "utf8"));
    entry.createdAt = Date.now() - 5 * 24 * 60 * 60 * 1000;
    await writeFile(file, JSON.stringify(entry), "utf8");

    const held = await replayPendingDetailed(ok, () => {}, { shouldReplay: () => false });
    assert.equal(held.deferred, 1);
    assert.equal(held.skipped, 0);
    assert.equal(held.cleaned, 0);
    const left = await listPending();
    assert.equal(left.length, 1, "a deferred item must survive its TTL");
    assert.equal(left[0].entry.retries, 0);

    // Once released, the TTL applies: the item is old and must be dropped, with
    // the loss attributed to it.
    const released = await replayPendingDetailed(ok, () => {}, { shouldReplay: () => true });
    assert.equal(released.skipped, 1);
    assert.equal(released.results[0].errorKind, "ttl_expired");
    assert.deepEqual(released.lost.map((item) => item.sessionId), ["ov-held"]);
    assert.deepEqual(await listPending(), []);
    delete process.env.OPENVIKING_PENDING_TTL_DAYS;
  });
});

test("a deferred item does not consume its retry budget", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "ov-budget", { content: "keep" });
    for (let i = 0; i < 5; i += 1) {
      await replayPendingDetailed(ok, () => {}, { shouldReplay: () => false });
    }
    const [entry] = await listPending();
    assert.equal(entry.entry.retries, 0);
    assert.ok(entry.filename.endsWith("_0.json"));
    assert.equal((await readdir(join(process.env.OPENVIKING_PENDING_DIR))).length, 1);
  });
});

test("a skipped item is always accounted for in lost, and a failed one never is", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "ov-accounted", { content: "rejected" });
    await enqueue("addMessage", "ov-accounted-2", { content: "flaky" });

    const result = await replayPendingDetailed(
      async (path, init) => {
        const body = JSON.parse(init.body);
        if (body.content === "rejected") return { ok: false, status: 413, error: { message: "too large" } };
        return { ok: false, status: 503, error: { message: "down" } };
      },
      () => {},
    );

    const skipped = result.results.filter((item) => item.status === "skipped");
    const failed = result.results.filter((item) => item.status === "failed");
    assert.equal(skipped.length, 1);
    assert.equal(failed.length, 1);
    // Everything reported as dropped is attributed; nothing that merely failed
    // is described as gone.
    assert.deepEqual(result.lost.map((entry) => entry.payload.content), ["rejected"]);
    assert.equal(result.cleaned, result.lost.length);
  });
});

test("a deletion that cannot happen is reported as failed, not as a drop", async (t) => {
  await withPendingDir(async (dir) => {
    const queued = await enqueue("addMessage", "ov-undeletable", { content: "stuck" });

    // The queue directory is replaced with a *file* of the same name while the
    // request is in flight. `claimForReplay` already succeeded by then, so the
    // failure lands exactly where it is meant to: on the unlink after a
    // non-retryable answer. Chmod cannot express this — a read-only directory
    // makes the claim fail first and the `dequeue_failed` branch is never
    // reached.
    const { rename, rm } = await import("node:fs/promises");
    const moved = `${dir}.moved`;
    const result = await replayPendingDetailed(
      async () => {
        await rename(dir, moved);
        await writeFile(dir, "not a directory", "utf8");
        return { ok: false, status: 413, error: { message: "too large" } };
      },
      () => {},
    );

    try {
      assert.equal(result.skipped, 0, "nothing was deleted, so nothing is lost");
      assert.equal(result.failed, 1);
      assert.equal(result.results[0].status, "failed");
      assert.equal(result.results[0].errorKind, "dequeue_failed");
      assert.deepEqual(result.lost, []);
      // The entry itself is still there, in the directory the test moved aside.
      assert.deepEqual(await readdir(moved), [`${queued.dedupKey}_0.processing`]);
    } finally {
      await rm(dir, { force: true });
      await rename(moved, dir).catch(() => {});
    }
  });
});

test("the legacy aggregate keeps its exact shape and the TTL is not counted twice", async () => {
  await withPendingDir(async (dir) => {
    process.env.OPENVIKING_PENDING_TTL_DAYS = "1";
    const queued = await enqueue("addMessage", "ov-legacy", { content: "old" });
    const file = join(dir, queued.path);
    const entry = JSON.parse(await (await import("node:fs/promises")).readFile(file, "utf8"));
    entry.createdAt = Date.now() - 5 * 24 * 60 * 60 * 1000;
    await writeFile(file, JSON.stringify(entry), "utf8");

    const legacy = await replayPending(ok, () => {});
    assert.deepEqual(legacy, { replayed: 0, failed: 0, skipped: 1, deferred: 0 });
    // The second pass must not see it again.
    const again = await replayPending(ok, () => {});
    assert.deepEqual(again, { replayed: 0, failed: 0, skipped: 0, deferred: 0 });
    assert.equal(await cleanStale(), 0);
    delete process.env.OPENVIKING_PENDING_TTL_DAYS;
  });
});
