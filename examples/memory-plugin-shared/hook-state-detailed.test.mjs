/**
 * The detailed state read and the per-operation replay.
 *
 * Both exist because an aggregate answer is not enough to act on: `{}` cannot
 * say whether a session has no state or a state file nobody may read, and four
 * counters cannot say which `commitPending` was settled. These tests assert the
 * distinctions themselves, not the shape of the objects that carry them.
 */

import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";

import {
  quarantineHookState,
  readHookState,
  readHookStateDetailed,
  withAgentHookLock,
  writeHookState,
} from "./lib/agent-hook-runtime.mjs";
import { enqueue, listPending, replayPending, replayPendingDetailed } from "./lib/pending-queue.mjs";
import { scrubOpenVikingEnv, withPendingDir } from "./testing/support.mjs";

const restoreEnv = scrubOpenVikingEnv();
process.on("exit", restoreEnv);

async function withStateDir(fn) {
  const { mkdtemp, rm } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const dir = await mkdtemp(join(tmpdir(), "ov-hook-state-"));
  const previous = process.env.OPENVIKING_HOOK_STATE_DIR;
  process.env.OPENVIKING_HOOK_STATE_DIR = dir;
  try {
    return await fn(dir);
  } finally {
    if (previous === undefined) delete process.env.OPENVIKING_HOOK_STATE_DIR;
    else process.env.OPENVIKING_HOOK_STATE_DIR = previous;
    await rm(dir, { recursive: true, force: true });
  }
}

test("readHookStateDetailed separates a session that never ran from one that broke", async () => {
  await withStateDir(async () => {
    const missing = await readHookStateDetailed("kimicode", "never-seen");
    assert.equal(missing.status, "missing");
    assert.deepEqual(missing.state, {});

    await writeHookState("kimicode", "healthy", { ackOffset: 42 });
    const ok = await readHookStateDetailed("kimicode", "healthy");
    assert.equal(ok.status, "ok");
    assert.equal(ok.state.ackOffset, 42);

    // The legacy reader still answers `{}` for every failure mode, which is
    // exactly why a caller that has to decide whether to reset needs the new one.
    assert.deepEqual(await readHookState("kimicode", "never-seen"), {});
  });
});

test("a state file that is not JSON is corrupt, and reading it writes nothing", async () => {
  await withStateDir(async (dir) => {
    const file = join(dir, "kimicode", "broken.json");
    await mkdir(join(dir, "kimicode"), { recursive: true });
    await writeFile(file, "{ this is not json", "utf8");

    const read = await readHookStateDetailed("kimicode", "broken");
    assert.equal(read.status, "corrupt");
    assert.ok(read.error);
    // Nothing moved: the read must not decide to quarantine on the caller's behalf.
    assert.equal(await readFile(file, "utf8"), "{ this is not json");

    await writeFile(file, "[1,2,3]", "utf8");
    assert.equal((await readHookStateDetailed("kimicode", "broken")).status, "corrupt");
  });
});

test("a state path that is a directory is unreadable, not corrupt", async () => {
  await withStateDir(async (dir) => {
    await mkdir(join(dir, "kimicode", "a-directory.json"), { recursive: true });
    const read = await readHookStateDetailed("kimicode", "a-directory");
    assert.equal(read.status, "unreadable");
    assert.ok(read.error);
  });
});

test("quarantineHookState keeps the bytes under a timestamped name", async () => {
  await withStateDir(async (dir) => {
    const file = join(dir, "kimicode", "s.json");
    await mkdir(join(dir, "kimicode"), { recursive: true });
    await writeFile(file, "garbage", "utf8");

    const moved = await quarantineHookState("kimicode", "s", { at: Date.UTC(2026, 0, 2, 3, 4, 5) });
    assert.equal(moved.ok, true);
    assert.match(moved.backup, /s\.json\.corrupt-2026-01-02T03-04-05-000Z$/);
    assert.equal(await readFile(moved.backup, "utf8"), "garbage");
    // The live path is free again, so the next write starts a clean generation.
    assert.equal((await readHookStateDetailed("kimicode", "s")).status, "missing");
  });
});

test("withAgentHookLock still serialises on the same state path", async () => {
  await withStateDir(async () => {
    const order = [];
    const held = withAgentHookLock("kimicode", "locked", async () => {
      order.push("first-in");
      await new Promise((resolve) => setTimeout(resolve, 80));
      order.push("first-out");
      return "done";
    });
    // Give the first holder time to create the lock directory.
    await new Promise((resolve) => setTimeout(resolve, 20));
    const second = withAgentHookLock("kimicode", "locked", async () => {
      order.push("second-in");
      return "second";
    });
    assert.equal(await held, "done");
    assert.equal(await second, "second");
    assert.deepEqual(order, ["first-in", "first-out", "second-in"]);
  });
});

test("replayPendingDetailed reports one result per operation, typed and keyed", async () => {
  await withPendingDir(async () => {
    // Distinct but recent: the TTL would drop an epoch-zero timestamp, and the
    // point of this test is the per-operation result, not expiry.
    const t0 = Date.now();
    await enqueue("addMessage", "ov-detailed", { content: "one" }, { createdAt: t0 });
    await enqueue("commitSession", "ov-detailed", { keep_recent_count: 0 }, { createdAt: t0 + 1 });

    const detailed = await replayPendingDetailed(async () => ({ ok: true }), () => {});
    assert.equal(detailed.replayed, 2);
    assert.equal(detailed.results.length, 2);
    assert.deepEqual(
      detailed.results.map((result) => [result.type, result.sessionId, result.status]),
      [["addMessage", "ov-detailed", "sent"], ["commitSession", "ov-detailed", "sent"]],
    );
    for (const result of detailed.results) assert.match(result.dedupKey, /^[0-9a-f]{64}$/);

    // The legacy counters are unchanged for callers that only read them.
    assert.deepEqual(await replayPending(async () => ({ ok: true }), () => {}), {
      replayed: 0, failed: 0, skipped: 0, deferred: 0,
    });
  });
});

test("shouldReplay defers before the claim and costs no retry", async () => {
  await withPendingDir(async () => {
    await enqueue("commitSession", "ov-defer", { keep_recent_count: 0 });
    await enqueue("addMessage", "ov-defer", { content: "message" });

    let sent = 0;
    const deferred = await replayPendingDetailed(
      async () => { sent += 1; return { ok: true }; },
      () => {},
      { shouldReplay: (entry) => entry.type !== "commitSession" },
    );
    assert.equal(sent, 1);
    assert.equal(deferred.replayed, 1);
    assert.equal(deferred.deferred, 1);
    const commit = deferred.results.find((result) => result.type === "commitSession");
    assert.equal(commit.status, "deferred");
    assert.equal(commit.errorKind, "predicate");

    const left = await listPending();
    assert.deepEqual(left.map((item) => item.entry.type), ["commitSession"]);
    assert.equal(left[0].entry.retries, 0);
    assert.ok(left[0].filename.endsWith("_0.json"));

    // Once the predicate lets it through it goes out with its retry budget intact.
    const later = await replayPendingDetailed(async () => ({ ok: true }), () => {});
    assert.equal(later.replayed, 1);
    assert.deepEqual(await listPending(), []);
  });
});

test("a failed addMessage defers the rest of the queue instead of calling it lost", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "ov-blocked", { content: "first" });
    await enqueue("addMessage", "ov-blocked", { content: "second" });
    await enqueue("addMessage", "ov-blocked", { content: "third" });

    const result = await replayPendingDetailed(
      async () => ({ ok: false, status: 503, error: { message: "unavailable" } }),
      () => {},
    );
    assert.equal(result.failed, 1);
    assert.equal(result.skipped, 0);
    assert.equal(result.deferred, 2);
    assert.deepEqual(
      result.results.map((item) => item.status),
      ["failed", "deferred", "deferred"],
    );
    assert.ok(result.results.every((item) => item.errorKind !== "ttl_expired"));
    assert.equal((await listPending()).length, 3);
  });
});

test("a claim another process holds is deferred, never skipped", async () => {
  await withPendingDir(async () => {
    const queued = await enqueue("addMessage", "ov-contended", { content: "held" });
    const { claimForReplay, releaseClaim } = await import("./lib/pending-queue.mjs");

    // The predicate is where another hook's claim can land: it runs after this
    // process listed the queue and before this process claims. Claiming inside
    // it reproduces exactly that interleaving.
    let claimed = null;
    const result = await replayPendingDetailed(
      async () => ({ ok: true }),
      () => {},
      {
        shouldReplay: async () => {
          claimed = await claimForReplay(queued.path);
          return true;
        },
      },
    );

    assert.ok(claimed);
    assert.equal(result.skipped, 0);
    assert.equal(result.deferred, 1);
    assert.equal(result.results[0].status, "deferred");
    assert.equal(result.results[0].errorKind, "claim_contended");

    assert.ok(await releaseClaim(claimed));
    assert.equal((await listPending()).length, 1);
  });
});

test("TTL cleanup attributes each dropped entry to its own operation", async () => {
  await withPendingDir(async (dir) => {
    process.env.OPENVIKING_PENDING_TTL_DAYS = "1";
    // `enqueue` takes the timestamp directly, so the order is deterministic
    // and nothing has to be rewritten behind the queue's back.
    const old = Date.now() - 3 * 24 * 60 * 60 * 1000;
    await enqueue("addMessage", "ov-ttl", { content: "old" }, { createdAt: old });
    await enqueue("commitSession", "ov-ttl", { keep_recent_count: 0 }, { createdAt: old + 1 });

    let calls = 0;
    const result = await replayPendingDetailed(async () => { calls += 1; return { ok: true }; }, () => {});
    assert.equal(calls, 0, "an expired entry must not be sent");
    assert.equal(result.cleaned, 2);
    assert.equal(result.skipped, 2);
    assert.deepEqual(
      result.results.map((item) => [item.type, item.status, item.errorKind]),
      [["addMessage", "skipped", "ttl_expired"], ["commitSession", "skipped", "ttl_expired"]],
    );
    assert.deepEqual(result.lost.map((entry) => entry.type).sort(), ["addMessage", "commitSession"]);
    assert.deepEqual(await listPending(), []);
    delete process.env.OPENVIKING_PENDING_TTL_DAYS;
  });
});

test("a non-retryable rejection is skipped with its status, and still named", async () => {
  await withPendingDir(async () => {
    await enqueue("addMessage", "ov-rejected", { content: "too large" });
    const result = await replayPendingDetailed(
      async () => ({ ok: false, status: 413, error: { message: "too large" } }),
      () => {},
    );
    assert.equal(result.skipped, 1);
    assert.equal(result.results[0].status, "skipped");
    assert.equal(result.results[0].errorKind, "http_413");
    assert.deepEqual(await listPending(), []);
  });
});
