import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { mkdir, mkdtemp, readFile, rename, rm, stat, utimes, writeFile } from "node:fs/promises";
import { syncBuiltinESMExports } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const STATE_DIR = await mkdtemp(join(tmpdir(), "ov-session-state-"));
process.env.OPENVIKING_CODEX_STATE_DIR = STATE_DIR;

const { clearEnded, markEnded, readEndedAt, withSessionLock } = await import("./session-state.mjs");

async function exists(path) {
  try { await stat(path); return true; } catch { return false; }
}

test("releasing a lock taken over by someone else leaves it alone", async () => {
  const dir = join(STATE_DIR, "takeover.lock");
  const outcome = await withSessionLock("takeover", async () => {
    // Simulate another taker that renamed our lock aside and re-created it.
    await rm(dir, { recursive: true, force: true });
    await mkdir(dir);
    await writeFile(join(dir, "owner"), "9999:other-owner");
    return "done";
  }, { waitMs: 0 });

  assert.equal(outcome.skipped, false);
  assert.equal(outcome.value, "done");
  assert.equal(await exists(dir), true, "the new holder's lock must survive our release");
  assert.equal(await readFile(join(dir, "owner"), "utf-8"), "9999:other-owner");
  await rm(dir, { recursive: true, force: true });
});

test("concurrent stale takeovers leave exactly one holder", async () => {
  const dir = join(STATE_DIR, "stale.lock");
  await mkdir(dir, { recursive: true });
  const old = new Date(Date.now() - 600_000);
  await utimes(dir, old, old);

  let inside = 0;
  let maxInside = 0;
  const run = () => withSessionLock("stale", async () => {
    inside += 1;
    maxInside = Math.max(maxInside, inside);
    await new Promise((resolve) => setTimeout(resolve, 120));
    inside -= 1;
    return true;
  }, { waitMs: 5_000, staleMs: 300_000 });

  const outcomes = await Promise.all([run(), run(), run()]);
  assert.equal(maxInside, 1, "a stale takeover must never hand the lock to two takers");
  assert.equal(outcomes.filter((o) => o.value === true).length, 3);
  assert.equal(await exists(dir), false, "the last holder releases the lock");
});

for (const scenario of ["successor", "heartbeat", "replacement", "release"]) {
  test(`a delayed stale claimant revalidates the lock after ${scenario}`, { timeout: 5_000 }, async (t) => {
    const sessionId = `delayed-${scenario}`;
    const dir = join(STATE_DIR, `${sessionId}.lock`);
    const ownerFile = join(dir, "owner");
    await mkdir(dir);
    await writeFile(ownerFile, "old-owner");
    const old = new Date(Math.floor(Date.now() / 1000) * 1000 - 600_000);
    await utimes(ownerFile, old, old);
    const observed = await stat(ownerFile);

    let paused, resume, entered, release;
    const claimantPaused = new Promise((resolve) => { paused = resolve; });
    const resumeClaimant = new Promise((resolve) => { resume = resolve; });
    const successorEntered = new Promise((resolve) => { entered = resolve; });
    const releaseSuccessor = new Promise((resolve) => { release = resolve; });
    const originalMkdir = fs.mkdir;
    let delayed = false;
    const hook = t.mock.method(fs, "mkdir", async (path, ...args) => {
      if (!delayed && path.startsWith(join(dir, "claim-"))) {
        delayed = true;
        if (scenario === "release") {
          await originalMkdir(path, ...args);
          await rm(dir, { recursive: true, force: true });
        }
        // Pause after observing the old stamp, before atomically claiming it.
        paused();
        await resumeClaimant;
        if (scenario === "release") return;
      }
      return originalMkdir(path, ...args);
    });
    syncBuiltinESMExports();

    let staleEntries = 0;
    const first = withSessionLock(sessionId, async () => { staleEntries += 1; });
    let second;
    try {
      await claimantPaused;
      if (scenario === "successor") {
        second = withSessionLock(sessionId, async () => {
          entered();
          await releaseSuccessor;
          return "live";
        });
        await successorEntered;
      } else if (scenario === "heartbeat") {
        const now = new Date();
        await utimes(ownerFile, now, now);
      } else if (scenario === "replacement") {
        const replacement = join(dir, "replacement");
        await writeFile(replacement, "old-owner");
        await utimes(replacement, old, old);
        await rename(replacement, ownerFile);
        const current = await stat(ownerFile);
        assert.equal(current.dev, observed.dev);
        assert.notEqual(current.ino, observed.ino);
        assert.equal(current.mtimeMs, observed.mtimeMs);
      }
      const liveOwner = scenario === "release" ? null : await readFile(ownerFile, "utf-8");
      resume();
      assert.deepEqual(await first, { skipped: true });
      assert.equal(staleEntries, 0, "the live successor keeps exclusive access");
      if (scenario !== "release") assert.equal(await readFile(ownerFile, "utf-8"), liveOwner);
      release();
      if (second) {
        assert.equal((await second).value, "live");
        assert.equal(await exists(dir), false);
      }
    } finally {
      resume();
      release();
      await Promise.allSettled([first, second]);
      hook.mock.restore();
      syncBuiltinESMExports();
      await rm(dir, { recursive: true, force: true });
    }
  });
}

test("a claim left behind by a dead taker does not wedge the lock", async () => {
  const dir = join(STATE_DIR, "wedged.lock");
  // A taker that died between winning its claim and stamping the lock.
  await mkdir(join(dir, "claim-deadbeef"), { recursive: true });
  const old = new Date(Date.now() - 600_000);
  await utimes(join(dir, "claim-deadbeef"), old, old);
  await utimes(dir, old, old);

  const outcome = await withSessionLock("wedged", async () => "taken", { waitMs: 0, staleMs: 300_000 });
  assert.equal(outcome.value, "taken", "the abandoned lock is still takeable");
  assert.equal(await exists(dir), false, "the release clears the abandoned claim with the lock");
});

test("clearEnded honours the `before` cutoff", async () => {
  const at = await markEnded("cutoff");
  assert.ok(at > 0);

  await clearEnded("cutoff", { before: at });
  assert.equal(await readEndedAt("cutoff"), at, "a marker at the cutoff is kept");

  await clearEnded("cutoff", { before: at - 1 });
  assert.equal(await readEndedAt("cutoff"), at, "a marker newer than the cutoff is kept");

  await clearEnded("cutoff", { before: at + 1 });
  assert.equal(await readEndedAt("cutoff"), undefined, "an older marker is removed");

  await markEnded("cutoff");
  await clearEnded("cutoff");
  assert.equal(await readEndedAt("cutoff"), undefined, "no cutoff clears unconditionally");
});

test("a clearEnded racing a newer markEnded leaves the newer marker", async () => {
  for (let i = 0; i < 500; i += 1) {
    const id = `race-${i}`;
    const at = await markEnded(id);
    // No artificial gap: the second exit lands in the same millisecond, while
    // the old caller's removal of everything up to `at` is in flight.
    const [, newer] = await Promise.all([
      clearEnded(id, { before: at + 1 }),
      markEnded(id),
    ]);
    const left = await readEndedAt(id);
    assert.equal(left, newer, "the newer marker must survive the concurrent clear");
    await clearEnded(id);
  }
});

test("markEnded / readEndedAt still honour a pre-0.8.1 bare marker", async () => {
  const at = Date.now() - 5_000;
  await writeFile(join(STATE_DIR, "legacy.ended"), String(at));
  assert.equal(await readEndedAt("legacy"), at);

  const newer = await markEnded("legacy");
  assert.equal(await readEndedAt("legacy"), newer, "the newest marker wins");

  await clearEnded("legacy", { before: at + 1 });
  assert.equal(await readEndedAt("legacy"), newer, "only the legacy marker was old enough");
  assert.equal(await exists(join(STATE_DIR, "legacy.ended")), false);

  await clearEnded("legacy");
  assert.equal(await readEndedAt("legacy"), undefined);
});

test.after(async () => {
  await rm(STATE_DIR, { recursive: true, force: true });
});
