/**
 * Hook wall-clock budget: every request gets the remainder, never a fresh
 * timeout, and an exhausted budget fails open instead of issuing a request
 * that the host will kill.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { boundFetch, createRemainingMs } from "./hook-budget.mjs";

test("requests are clamped to the remaining budget", async () => {
  let clock = 1000;
  const remainingMs = createRemainingMs(5000, () => clock);
  const timeouts = [];
  const fetchJSON = async (path, init, options) => {
    timeouts.push(options.timeoutMs);
    return { ok: true, status: 200 };
  };
  const bounded = boundFetch(fetchJSON, remainingMs, 15000);

  await bounded("/health");
  assert.equal(timeouts[0], 5000);

  clock += 4000;
  await bounded("/messages", {}, { timeoutMs: 3000 });
  assert.equal(timeouts[1], 1000);
});

test("an exhausted budget stops issuing requests", async () => {
  let clock = 0;
  const remainingMs = createRemainingMs(1000, () => clock);
  let calls = 0;
  const bounded = boundFetch(async () => {
    calls += 1;
    return { ok: true, status: 200 };
  }, remainingMs, 15000);

  clock += 2000;
  const result = await bounded("/health");
  assert.equal(result.ok, false);
  assert.equal(result.status, 0);
  assert.equal(calls, 0);
});
