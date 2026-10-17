/**
 * Commit gate (DESIGN.md §5.5): a commit may not archive across a payload the
 * run failed to deliver, an entry still sitting in the pending queue, or an
 * older queue item from the same session.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  advanceCapturedSinceCommit,
  canCommitCapture,
  shouldQueueSessionEndCommit,
} from "./ov-session.mjs";

const clean = { sent: 8, failed: 0, queued: 0, enqueueFailed: 0 };
const opts = {
  event: "stop",
  cfg: { commitTurnThreshold: 8 },
  previousCaptured: 0,
  pendingCount: 0,
};

test("clean run past the threshold commits", () => {
  assert.equal(canCommitCapture(clean, opts), true);
});

test("rejected payloads block the commit", () => {
  assert.equal(canCommitCapture({ ...clean, sent: 7, failed: 1 }, opts), false);
});

test("queued payloads block the commit", () => {
  assert.equal(canCommitCapture({ ...clean, sent: 7, queued: 1 }, opts), false);
});

test("enqueue failures block the commit", () => {
  assert.equal(canCommitCapture({ ...clean, sent: 7, enqueueFailed: 1 }, opts), false);
});

test("an older pending item blocks the commit", () => {
  assert.equal(canCommitCapture(clean, { ...opts, pendingCount: 1 }), false);
});

test("nothing new never commits", () => {
  assert.equal(canCommitCapture({ ...clean, sent: 0 }, { ...opts, event: "session-end" }), false);
});

test("session-end still respects stranded payloads", () => {
  assert.equal(canCommitCapture({ ...clean, sent: 3, failed: 1 }, { ...opts, event: "session-end" }), false);
});

test("threshold progress counts payloads, not acknowledgement keys", () => {
  // One turn whose tool parts acknowledge three keys still advances by one.
  assert.equal(advanceCapturedSinceCommit(5, 1), 6);
  assert.equal(advanceCapturedSinceCommit(6, 2), 8);
});

test("session-end queues a commit only behind persisted messages", () => {
  assert.equal(shouldQueueSessionEndCommit("session-end", clean, 1), true);
  assert.equal(shouldQueueSessionEndCommit("session-end", clean, 0), false);
  assert.equal(shouldQueueSessionEndCommit("stop", clean, 1), false);
  assert.equal(shouldQueueSessionEndCommit("session-end", { ...clean, failed: 1 }, 1), false);
  assert.equal(shouldQueueSessionEndCommit("session-end", { ...clean, enqueueFailed: 1 }, 1), false);
});
