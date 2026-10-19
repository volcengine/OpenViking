/**
 * Session lock wiring (DESIGN.md §5.5): the state directory may not exist yet on
 * a fresh install — capture must create it, not skip the hook — and the lock
 * lives beside this plugin's own state.
 *
 * The algorithm (owner stamp, stale takeover, one winner per reclaim) is the
 * shared withDirLock's and is covered once in the shared suite; repeating it
 * here would only prove the copy still says what the shared version says.
 */

import assert from "node:assert/strict";
import { mkdtemp, rm, stat } from "node:fs/promises";
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

test("the lock sits beside this plugin's state and is gone afterwards", async () => {
  await withStateDir(async () => {
    let seen = null;
    await withSessionLock("kc-lock", async () => {
      seen = (await stat(join(getStateDir(), `${safeId("kc-lock")}.lock`))).isDirectory();
    });
    assert.equal(seen, true);
    await assert.rejects(stat(join(getStateDir(), `${safeId("kc-lock")}.lock`)));
  });
});

test("a busy lock is reported as a skipped hook, not as success", async () => {
  await withStateDir(async () => {
    let release;
    let started;
    const gate = new Promise((resolve) => { release = resolve; });
    const ready = new Promise((resolve) => { started = resolve; });

    const holder = withSessionLock("kc-busy", async () => {
      started();
      await gate;
      return "held";
    });
    await ready;

    // The capture hook passes its remaining budget as waitMs; at zero it must
    // come back with null so the caller skips instead of running unserialized.
    assert.equal(await withSessionLock("kc-busy", async () => "second", { waitMs: 0 }), null);

    release();
    assert.equal(await holder, "held");
  });
});
