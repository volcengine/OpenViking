/**
 * Recall cache: duplicate suppression, reuse window, and the per-session file.
 */

import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  RECALL_CACHE_TTL_MS,
  decideRecall,
  readRecallCache,
  writeRecallCache,
} from "./recall-cache.mjs";

const hash = "a".repeat(64);

test("a different or missing prompt is a miss", () => {
  assert.equal(decideRecall(null, hash, 1000), "miss");
  assert.equal(decideRecall({ hash: "b".repeat(64), at: 900, block: "x" }, hash, 1000), "miss");
});

test("an immediate re-delivery is a duplicate", () => {
  assert.equal(decideRecall({ hash, at: 800, block: "x" }, hash, 1000), "duplicate");
});

test("a repeated prompt inside the TTL reuses the block", () => {
  assert.equal(decideRecall({ hash, at: 1000, block: "x" }, hash, 6000), "hit");
});

test("an expired entry or an empty block is a miss", () => {
  const now = 1000;
  assert.equal(decideRecall({ hash, at: now, block: "x" }, hash, now + RECALL_CACHE_TTL_MS + 1), "miss");
  assert.equal(decideRecall({ hash, at: 1000, block: "" }, hash, 6000), "miss");
});

test("the cache round-trips through the state directory", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-recall-cache-"));
  const previous = process.env.OPENVIKING_KIMICODE_STATE_DIR;
  process.env.OPENVIKING_KIMICODE_STATE_DIR = join(root, "state");
  try {
    assert.equal(await readRecallCache("kc-cache"), null);
    await writeRecallCache("kc-cache", { hash, at: 123, block: "memory" });
    assert.deepEqual(await readRecallCache("kc-cache"), { hash, at: 123, block: "memory" });
  } finally {
    if (previous === undefined) delete process.env.OPENVIKING_KIMICODE_STATE_DIR;
    else process.env.OPENVIKING_KIMICODE_STATE_DIR = previous;
    await rm(root, { recursive: true, force: true });
  }
});
