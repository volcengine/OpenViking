import assert from "node:assert/strict";
import { mkdtemp, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  claimForReplay,
  drainPendingForSession,
  enqueue,
  listPending,
} from "./lib/pending-queue.mjs";

const originalEnv = {
  dir: process.env.OPENVIKING_PENDING_DIR,
  maxRetries: process.env.OPENVIKING_PENDING_MAX_RETRIES,
  replayLimit: process.env.OPENVIKING_PENDING_REPLAY_LIMIT,
  ttlDays: process.env.OPENVIKING_PENDING_TTL_DAYS,
};

async function withPendingDir(fn) {
  const dir = await mkdtemp(join(tmpdir(), "openviking-shared-pending-test-"));
  process.env.OPENVIKING_PENDING_DIR = dir;
  process.env.OPENVIKING_PENDING_MAX_RETRIES = "3";
  process.env.OPENVIKING_PENDING_REPLAY_LIMIT = "50";
  process.env.OPENVIKING_PENDING_TTL_DAYS = "7";
  try {
    return await fn(dir);
  } finally {
    for (const [key, envName] of [
      ["dir", "OPENVIKING_PENDING_DIR"],
      ["maxRetries", "OPENVIKING_PENDING_MAX_RETRIES"],
      ["replayLimit", "OPENVIKING_PENDING_REPLAY_LIMIT"],
      ["ttlDays", "OPENVIKING_PENDING_TTL_DAYS"],
    ]) {
      if (originalEnv[key] === undefined) delete process.env[envName];
      else process.env[envName] = originalEnv[key];
    }
    await rm(dir, { recursive: true, force: true });
  }
}

test("session drain bypasses an older retryable failure from another session", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "other-session", { content: "other" }, { createdAt: 1 });
    await enqueue("addMessage", "takeover-session", { content: "current" }, { createdAt: 2 });

    const calls = [];
    const result = await drainPendingForSession(async (path) => {
      calls.push(path);
      return { ok: true };
    }, () => {}, "takeover-session");

    assert.deepEqual(calls, ["/api/v1/sessions/takeover-session/messages"]);
    assert.deepEqual(result, {
      replayed: 1,
      failed: 0,
      skipped: 0,
      deferred: 0,
      remaining: 0,
    });
    const pending = await listPending();
    assert.equal(pending.length, 1);
    assert.equal(pending[0].entry.sessionId, "other-session");
  });
});

test("session drain reports messages left by its item budget", async () => {
  await withPendingDir(async () => {
    for (let i = 0; i < 3; i++) {
      await enqueue("addMessage", "takeover-session", { content: `message-${i}` }, { createdAt: i + 1 });
    }

    let calls = 0;
    const result = await drainPendingForSession(async () => {
      calls++;
      return { ok: true };
    }, () => {}, "takeover-session", { maxItems: 1 });

    assert.equal(calls, 1);
    assert.equal(result.replayed, 1);
    assert.equal(result.deferred, 2);
    assert.equal(result.remaining, 2);
  });
});

test("session drain item budget also bounds entries skipped without HTTP", async () => {
  await withPendingDir(async () => {
    process.env.OPENVIKING_PENDING_MAX_RETRIES = "0";
    await enqueue("addMessage", "takeover-session", { content: "expired" }, { createdAt: 1 });
    await enqueue("addMessage", "takeover-session", { content: "deferred" }, { createdAt: 2 });

    let calls = 0;
    const result = await drainPendingForSession(async () => {
      calls++;
      return { ok: true };
    }, () => {}, "takeover-session", { maxItems: 1 });

    assert.equal(calls, 0);
    assert.equal(result.skipped, 1);
    assert.equal(result.deferred, 1);
    assert.equal(result.remaining, 1);
  });
});

test("session drain starts no work after its elapsed-time budget is exhausted", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "takeover-session", { content: "later" }, { createdAt: 1 });

    let calls = 0;
    const result = await drainPendingForSession(async () => {
      calls++;
      return { ok: true };
    }, () => {}, "takeover-session", { timeBudgetMs: 0 });

    assert.equal(calls, 0);
    assert.equal(result.deferred, 1);
    assert.equal(result.remaining, 1);
  });
});

test("session drain attempts a retryable message at most once per invocation", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "takeover-session", { content: "blocked" }, { createdAt: 1 });
    await enqueue("addMessage", "takeover-session", { content: "after" }, { createdAt: 2 });

    let calls = 0;
    const result = await drainPendingForSession(async () => {
      calls++;
      return { ok: false, status: 500 };
    }, () => {}, "takeover-session", { maxItems: 50 });

    assert.equal(calls, 1);
    assert.equal(result.failed, 1);
    assert.equal(result.deferred, 1);
    assert.equal(result.remaining, 2);
    const pending = await listPending();
    assert.deepEqual(pending.map(({ entry }) => entry.retries), [1, 0]);
  });
});

test("session drain leaves queued commits to the normal replay path", async () => {
  await withPendingDir(async () => {
    await enqueue("commitSession", "takeover-session", { keep_recent_count: 1 }, { createdAt: 1 });

    let calls = 0;
    const result = await drainPendingForSession(async () => {
      calls++;
      return { ok: true };
    }, () => {}, "takeover-session");

    assert.equal(calls, 0);
    assert.equal(result.remaining, 0);
    assert.equal((await listPending()).length, 1);
  });
});

test("session drain keeps the barrier closed for a claimed in-flight message", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "takeover-session", { content: "in flight" }, { createdAt: 1 });
    const [{ filename }] = await listPending();
    assert.match(await claimForReplay(filename), /\.processing$/);

    const result = await drainPendingForSession(async () => {
      assert.fail("an already claimed message must not be replayed twice");
    }, () => {}, "takeover-session");

    assert.equal(result.replayed, 0);
    assert.equal(result.remaining, 1);
  });
});

test("session drain conservatively counts a queue filename that changes during inventory", async () => {
  await withPendingDir(async (dir) => {
    await symlink(join(dir, "already-renamed.processing"), join(dir, "raced_0.json"));

    const result = await drainPendingForSession(async () => {
      assert.fail("an unresolved inventory entry must not be replayed");
    }, () => {}, "takeover-session");

    assert.equal(result.remaining, 1);
  });
});
