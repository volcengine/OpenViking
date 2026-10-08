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
    // skipCommitFor keeps this test focused on the write-replay mechanics; the
    // commit-after-replay behavior has its own tests below.
    const result = await replayPending(async (path, init) => {
      calls.push({ path, init });
      return { ok: true };
    }, () => {}, { skipCommitFor: ["queue-replay"] });

    assert.deepEqual(result, {
      replayed: 1,
      failed: 0,
      skipped: 0,
      deferred: 0,
      commitsSent: 0,
      commitsQueued: 0,
    });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].path, "/api/v1/sessions/queue-replay/messages");
    assert.deepEqual(JSON.parse(calls[0].init.body), payload);
    assert.deepEqual(await listPending(), []);
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

test("replayPending commits a finished session whose writes it just replayed", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "cc-finished", { role: "user", content: "one" });
    await enqueue("addMessage", "cc-finished", { role: "assistant", content: "two" });

    const calls = [];
    const result = await replayPending(
      async (path, init) => {
        calls.push({ path, init });
        return { ok: true };
      },
      () => {},
      { skipCommitFor: ["cc-current"] },
    );

    assert.deepEqual(result, {
      replayed: 2,
      failed: 0,
      skipped: 0,
      deferred: 0,
      commitsSent: 1,
      commitsQueued: 0,
    });
    assert.equal(
      calls.filter((call) => call.path === "/api/v1/sessions/cc-finished/messages").length,
      2,
    );
    const commitIndex = calls.findIndex((call) => call.path === "/api/v1/sessions/cc-finished/commit");
    assert.ok(commitIndex !== -1, "expected a commit for the replayed finished session");
    // The commit must land after both replayed writes.
    assert.ok(commitIndex > 1);
    assert.deepEqual(JSON.parse(calls[commitIndex].init.body), {});
    assert.deepEqual(await listPending(), []);
  });
});

test("replayPending skips the commit for the caller's own session", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "cc-current", { role: "user", content: "mine" });

    const calls = [];
    const result = await replayPending(
      async (path, init) => {
        calls.push({ path, init });
        return { ok: true };
      },
      () => {},
      { skipCommitFor: ["cc-current"] },
    );

    assert.deepEqual(result, {
      replayed: 1,
      failed: 0,
      skipped: 0,
      deferred: 0,
      commitsSent: 0,
      commitsQueued: 0,
    });
    assert.ok(calls.every((call) => !call.path.endsWith("/commit")));
    assert.deepEqual(await listPending(), []);
  });
});

test("replayPending parks the commit while writes of the session remain queued", async () => {
  await withPendingDir(async () => {
    process.env.OPENVIKING_PENDING_REPLAY_LIMIT = "1";
    await enqueue("addMessage", "cc-finished", { role: "user", content: "one" });
    await enqueue("addMessage", "cc-finished", { role: "assistant", content: "two" });

    const calls = [];
    const fetch = async (path, init) => {
      calls.push({ path, init });
      return { ok: true };
    };

    const first = await replayPending(fetch, () => {});
    assert.equal(first.replayed, 1);
    assert.equal(first.deferred, 1);
    assert.equal(first.commitsSent, 0);
    assert.ok(calls.every((call) => !call.path.endsWith("/commit")));
    assert.equal((await listPending()).length, 1);

    delete process.env.OPENVIKING_PENDING_REPLAY_LIMIT;
    const second = await replayPending(fetch, () => {});
    assert.equal(second.replayed, 1);
    assert.equal(second.commitsSent, 1);
    assert.ok(calls.some((call) => call.path === "/api/v1/sessions/cc-finished/commit"));
    assert.deepEqual(await listPending(), []);
  });
});

test("replayPending queues the commit when the commit call fails retryably", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "cc-finished", { role: "user", content: "one" });

    const result = await replayPending(async (path) => {
      if (path.endsWith("/commit")) {
        return { ok: false, status: 503, error: { message: "unavailable" } };
      }
      return { ok: true };
    }, () => {});

    assert.equal(result.replayed, 1);
    assert.equal(result.commitsSent, 0);
    assert.equal(result.commitsQueued, 1);
    const pending = await listPending();
    assert.equal(pending.length, 1);
    assert.equal(pending[0].entry.type, "commitSession");
    assert.equal(pending[0].entry.sessionId, "cc-finished");
  });
});

test("replayPending drops the commit when the commit call fails non-retryably", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "cc-finished", { role: "user", content: "one" });

    const result = await replayPending(async (path) => {
      if (path.endsWith("/commit")) {
        return { ok: false, status: 404, error: { code: "NOT_FOUND", message: "no session" } };
      }
      return { ok: true };
    }, () => {});

    assert.equal(result.replayed, 1);
    assert.equal(result.commitsSent, 0);
    assert.equal(result.commitsQueued, 0);
    assert.deepEqual(await listPending(), []);
  });
});
