import assert from "node:assert/strict";
import { readdir, utimes } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";

import {
  claimForReplay,
  deferCommitBehindPendingWrites,
  enqueue,
  listPending,
  replayPending,
} from "./lib/pending-queue.mjs";
import { createOvHttp } from "./lib/ov-http.mjs";
import { withMockOpenViking, withPendingDir, writeJson } from "./testing/support.mjs";

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

for (const type of ["addMessage", "commitSession"]) {
  test(`replayPending keeps an old ${type} backlog claimed during its HTTP request`, { timeout: 20_000 }, async () => {
    await withPendingDir(async (dir) => {
      const payload = type === "addMessage" ? { role: "user", content: "backlog" } : { keep_recent_count: 0 };
      const queued = await enqueue(type, "queue-old-backlog", payload);
      const old = new Date(Date.now() - 11 * 60_000);
      await utimes(join(dir, queued.path), old, old);

      let arrived;
      let release;
      const firstArrived = new Promise((resolve) => { arrived = resolve; });
      const held = new Promise((resolve) => { release = resolve; });
      let count = 0;
      await withMockOpenViking(async (req, res) => {
        for await (const chunk of req) {} // Drain the real HTTP request body.
        if (++count === 1) {
          arrived();
          await held;
        }
        writeJson(res, { status: "ok", result: {} });
      }, async (baseUrl, requests) => {
        const fetchJSON = createOvHttp({ baseUrl }, { defaultTimeoutMs: 10_000 });
        const first = replayPending(fetchJSON, () => {});
        let waitTimer;
        const deadline = new Promise((resolve, reject) => {
          waitTimer = setTimeout(() => reject(new Error("timed out waiting for the replay request")), 5_000);
        });
        let second;
        try {
          await Promise.race([
            firstArrived,
            first.then(() => { throw new Error("replay completed without sending the queued operation"); }),
            deadline,
          ]);
          second = await replayPending(fetchJSON, () => {});
        } finally {
          clearTimeout(waitTimer);
          release();
        }
        const firstResult = await first;

        assert.equal(requests.length, 1);
        assert.equal(firstResult.replayed, 1);
        assert.equal(second.replayed, 0);
        assert.deepEqual(await listPending(), []);
      });
    });
  });
}

async function queueWrites(sessionId, count, createdAt) {
  for (let i = 0; i < count; i++) {
    await enqueue("addMessage", sessionId, { role: "user", content: `parked ${i}` }, {
      createdAt: createdAt + i,
    });
  }
}

async function queueOrder() {
  return (await listPending()).map(({ entry }) => [entry.type, entry.sessionId]);
}

test("a commit goes out directly when its session has no queued writes", async () => {
  await withPendingDir(async () => {
    await queueWrites("other-session", 1, Date.now());
    const calls = [];
    const deferred = await deferCommitBehindPendingWrites(async (path) => {
      calls.push(path);
      return { ok: true };
    }, "idle-session");

    assert.equal(deferred, null);
    assert.deepEqual(calls, []);
    assert.deepEqual(await queueOrder(), [["addMessage", "other-session"]]);
  });
});

test("a commit waits for its session's queued writes to replay first", async () => {
  await withPendingDir(async () => {
    await queueWrites("parked-session", 2, Date.now());
    const calls = [];
    const deferred = await deferCommitBehindPendingWrites(async (path) => {
      calls.push(path);
      return { ok: true };
    }, "parked-session");

    assert.equal(deferred, null);
    assert.deepEqual(calls, Array(2).fill("/api/v1/sessions/parked-session/messages"));
    assert.deepEqual(await listPending(), []);
  });
});

test("a commit that cannot follow its writes now is queued behind them, replacing an older one", async () => {
  await withPendingDir(async () => {
    // A commit left over from an earlier failure, and writes stamped ahead of
    // the clock the way a failed batch remainder is.
    await enqueue("commitSession", "down-session", {}, { createdAt: Date.now() - 1000 });
    await queueWrites("down-session", 3, Date.now() + 60_000);

    const deferred = await deferCommitBehindPendingWrites(
      async () => ({ ok: false, status: 503 }),
      "down-session",
    );

    assert.equal(deferred.ok, true);
    assert.deepEqual(await queueOrder(), [
      ...Array(3).fill(["addMessage", "down-session"]),
      ["commitSession", "down-session"],
    ]);

    const calls = [];
    await replayPending(async (path) => {
      calls.push(path);
      return { ok: true };
    }, () => {});
    assert.deepEqual(calls, [
      ...Array(3).fill("/api/v1/sessions/down-session/messages"),
      "/api/v1/sessions/down-session/commit",
    ]);
  });
});
