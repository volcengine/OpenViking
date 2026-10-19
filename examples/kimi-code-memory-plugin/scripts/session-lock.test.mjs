/**
 * Session lock acquisition (DESIGN.md §5.5): the state directory may not
 * exist yet on a fresh install — capture must create it, not skip the hook.
 */

import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, stat, utimes } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { getStateDir, safeId, withSessionLock } from "./session-state.mjs";

async function withStateDir(fn) {
  const root = await mkdtemp(join(tmpdir(), "ov-lock-test-"));
  const previous = process.env.OPENVIKING_KIMICODE_STATE_DIR;
  process.env.OPENVIKING_KIMICODE_STATE_DIR = join(root, "nested", "state");
  try {
    await fn();
  } finally {
    if (previous === undefined) delete process.env.OPENVIKING_KIMICODE_STATE_DIR;
    else process.env.OPENVIKING_KIMICODE_STATE_DIR = previous;
    await rm(root, { recursive: true, force: true });
  }
}

test("a missing state directory is created before the lock", async () => {
  await withStateDir(async () => {
    const result = await withSessionLock("kc-lock", async () => "ran");
    assert.equal(result, "ran");
  });
});

test("the lock is released after the callback", async () => {
  await withStateDir(async () => {
    assert.equal(await withSessionLock("kc-lock", async () => 1), 1);
    assert.equal(await withSessionLock("kc-lock", async () => 2), 2);
  });
});

test("a stale holder cannot release the new owner's lock", async () => {
  await withStateDir(async () => {
    const id = "kc-race";
    const lockDir = join(getStateDir(), `${safeId(id)}.lock`);
    let releaseA;
    let releaseB;
    let startedA;
    let startedB;
    const gateA = new Promise((resolve) => { releaseA = resolve; });
    const gateB = new Promise((resolve) => { releaseB = resolve; });
    const readyA = new Promise((resolve) => { startedA = resolve; });
    const readyB = new Promise((resolve) => { startedB = resolve; });

    const holderA = withSessionLock(id, async () => {
      startedA();
      await gateA;
      return "a";
    });
    await readyA;

    // Age the lock past LOCK_STALE_MS so B reclaims it while A is still inside.
    const old = new Date(Date.now() - 120_000);
    await utimes(lockDir, old, old);

    const holderB = withSessionLock(id, async () => {
      startedB();
      await gateB;
      return "b";
    });
    await readyB;

    releaseA();
    assert.equal(await holderA, "a");
    // A finished late; the lock now belongs to B and must survive.
    assert.equal((await stat(lockDir)).isDirectory(), true);

    releaseB();
    assert.equal(await holderB, "b");
    await assert.rejects(stat(lockDir));
  });
});

test("two waiters reclaiming the same stale lock never both enter", async () => {
  await withStateDir(async () => {
    const id = "kc-double-stale";
    const lockDir = join(getStateDir(), `${safeId(id)}.lock`);
    await mkdir(lockDir, { recursive: true });
    const old = new Date(Date.now() - 120_000);
    await utimes(lockDir, old, old);

    let inside = 0;
    let peak = 0;
    const enter = async (value) => {
      inside += 1;
      peak = Math.max(peak, inside);
      await new Promise((resolve) => setTimeout(resolve, 20));
      inside -= 1;
      return value;
    };

    const results = await Promise.all([
      withSessionLock(id, () => enter("b")),
      withSessionLock(id, () => enter("c")),
    ]);

    assert.deepEqual(results.sort(), ["b", "c"]);
    // The loser of the reclaim must not delete the winner's fresh lock and then
    // take it too; that would put two capture hooks in the critical section.
    assert.equal(peak, 1);
    await assert.rejects(stat(lockDir));
  });
});
