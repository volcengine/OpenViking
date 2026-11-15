/**
 * The prompt budget is split, not blindly shared.
 *
 * Capture runs first in the same entry. Timing recall from process start let a
 * slow capture consume recall's window before recall began; these assertions
 * pin the reserve and the per-stage deadline.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  BUDGETS,
  RECALL_MIN_RUN_MS,
  captureDeadlineFor,
  recallDeadlineFor,
} from "../../scripts/hook-stage.mjs";

test("capture stops before the recall window and leaves it runnable", () => {
  const entry = 20_000;
  assert.equal(captureDeadlineFor(entry), entry - BUDGETS.recall);
  assert.ok(BUDGETS.recall < BUDGETS["user-prompt"]);
  assert.ok(
    BUDGETS["user-prompt"] - BUDGETS.recall >= RECALL_MIN_RUN_MS,
    "the entry budget must leave recall a runnable window",
  );
});

test("recall is timed from its own start and never outlives the entry", () => {
  const entry = 12_000;
  assert.equal(recallDeadlineFor(entry, 500), 500 + BUDGETS.recall);
  assert.equal(recallDeadlineFor(entry, 9_500), entry);
});
