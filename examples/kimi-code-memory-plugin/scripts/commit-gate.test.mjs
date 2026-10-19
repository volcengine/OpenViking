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
  nextCursor,
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

test("session-end commits progress an earlier stop left uncommitted", () => {
  // The last Stop already sent every turn, so this run plans nothing; the
  // session still has to be archived rather than waiting for a commit that
  // will never come.
  assert.equal(
    canCommitCapture(
      { sent: 0, failed: 0, queued: 0, enqueueFailed: 0 },
      { ...opts, event: "session-end", previousCaptured: 5 },
    ),
    true,
  );
});

test("a stopped cursor never skips a turn whose payload was not confirmed", () => {
  const plan = [
    { turn: { turnId: "0" }, keys: ["0:user", "0:assistant"] },
    { turn: { turnId: "1" }, keys: ["1:user"] },
  ];
  const acked = new Set(["0:user", "0:assistant", "1:user"]);
  assert.equal(nextCursor(plan, acked, ["0", "1", "2"]), "2");
  acked.delete("1:user");
  assert.equal(nextCursor(plan, acked, ["0", "1", "2"]), "0");
});

test("one unconfirmed entry holds the cursor for its whole turn", () => {
  // Two entries share turn 3; a map keyed by turnId would keep only the last
  // one's keys and let the cursor walk past the unconfirmed first entry.
  const plan = [
    { turn: { turnId: "3" }, keys: ["3:user"] },
    { turn: { turnId: "3" }, keys: ["3:assistant:tc1:call"] },
  ];
  assert.equal(nextCursor(plan, new Set(["3:assistant:tc1:call"]), ["3"]), null);
});

test("a tool-only turn the run could not confirm holds the cursor", () => {
  const plan = [
    { turn: { turnId: "1" }, keys: ["1:assistant:tc1:call"] },
    { turn: { turnId: "2" }, keys: ["2:user"] },
  ];
  assert.equal(nextCursor(plan, new Set(["2:user"]), ["1", "2"]), null);
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
