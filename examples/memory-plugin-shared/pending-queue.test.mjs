import assert from "node:assert/strict";
import { readdir, utimes } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";

import {
  claimForReplay,
  enqueue,
  listPending,
  replayPending,
  trimPending,
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

test("enqueue evicts the oldest entries once the queue reaches its cap", async () => {
  await withPendingDir(async (dir) => {
    process.env.OPENVIKING_PENDING_MAX_ENTRIES = "10";
    const base = Date.now() - 60_000;
    for (let i = 0; i < 10; i++) {
      const queued = await enqueue("addMessage", "queue-cap", { role: "user", content: `m${i}` });
      assert.equal(queued.evicted, 0);
      const at = new Date(base + i * 1000);
      await utimes(join(dir, queued.path), at, at);
    }

    const overflow = await enqueue("addMessage", "queue-cap", { role: "user", content: "m10" });

    assert.equal(overflow.ok, true);
    assert.equal(overflow.evicted, 2);
    const contents = (await listPending()).map(({ entry }) => entry.payload.content);
    assert.deepEqual(contents.sort(), ["m10", "m2", "m3", "m4", "m5", "m6", "m7", "m8", "m9"]);
  });
});

test("replayPending trims a backlog already over the cap without an enqueue", async () => {
  await withPendingDir(async () => {
    for (let i = 0; i < 30; i++) {
      await enqueue("addMessage", "queue-backlog", { role: "user", content: `m${i}` });
    }
    process.env.OPENVIKING_PENDING_MAX_ENTRIES = "10";
    process.env.OPENVIKING_PENDING_REPLAY_LIMIT = "1";

    await replayPending(async () => ({ ok: false, status: 503 }), () => {});

    assert.ok((await listPending()).length <= 10);
  });
});

test("trimPending leaves a queue at the cap untouched", async () => {
  await withPendingDir(async () => {
    process.env.OPENVIKING_PENDING_MAX_ENTRIES = "3";
    for (let i = 0; i < 3; i++) {
      await enqueue("addMessage", "queue-at-cap", { role: "user", content: `m${i}` });
    }

    assert.equal(await trimPending(), 0);
    assert.equal((await listPending()).length, 3);
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
