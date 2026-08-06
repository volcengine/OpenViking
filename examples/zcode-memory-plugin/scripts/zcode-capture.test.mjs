import assert from "node:assert/strict";
import test from "node:test";

import {
  applyZcodeCaptureResult,
  buildZcodeCapturePlan,
} from "./zcode-capture.mjs";

const turns = [
  { role: "user", content: "question", turnId: "turn-001" },
  { role: "assistant", content: "answer", turnId: "turn-001" },
];

test("capture plan maps host identity to the OpenViking turn_id contract", () => {
  const plan = buildZcodeCapturePlan(turns, {});
  assert.deepEqual(plan.payloads, [
    { role: "user", content: "question", turn_id: "turn-001" },
    { role: "assistant", content: "answer", turn_id: "turn-001" },
  ]);
  assert.equal(plan.payloads[0].turnId, undefined);
});

test("non-retryable failure keeps cursor, dedup, and pending prompt unchanged", () => {
  const state = {
    lastTurnId: "turn-000",
    capturedTurnIds: [],
    pendingPrompt: { prompt: "question", hash: "prompt-hash" },
  };
  const plan = buildZcodeCapturePlan(turns, state);
  const next = applyZcodeCaptureResult(state, plan, {
    sent: 0,
    queued: 0,
    failed: 2,
  });

  assert.equal(next.lastTurnId, "turn-000");
  assert.deepEqual(next.capturedTurnIds, []);
  assert.deepEqual(next.pendingPrompt, state.pendingPrompt);
  assert.equal(next.captured, 0);
});

test("only the acknowledged contiguous prefix updates dedup and cursor state", () => {
  const state = {
    lastTurnId: "turn-000",
    capturedTurnIds: [],
    pendingPrompt: { prompt: "question" },
  };
  const plan = buildZcodeCapturePlan(turns, state);
  const next = applyZcodeCaptureResult(state, plan, {
    sent: 1,
    queued: 0,
    failed: 1,
  });

  assert.deepEqual(next.capturedTurnIds, ["turn-001:user"]);
  assert.equal(next.lastTurnId, "turn-000");
  assert.equal(next.pendingPrompt, null);
});

test("successful capture records every dedup key and suppresses repeated Stop", () => {
  const state = {
    capturedTurnIds: [],
    pendingPrompt: { prompt: "question" },
  };
  const firstPlan = buildZcodeCapturePlan(turns, state);
  const next = applyZcodeCaptureResult(state, firstPlan, {
    sent: 2,
    queued: 0,
    failed: 0,
  });

  assert.deepEqual(next.capturedTurnIds, [
    "turn-001:user",
    "turn-001:assistant",
  ]);
  assert.equal(next.lastTurnId, "turn-001");
  assert.equal(next.pendingPrompt, null);

  const repeatedPlan = buildZcodeCapturePlan(turns, next);
  assert.equal(repeatedPlan.toSend.length, 0);
  assert.equal(repeatedPlan.payloads.length, 0);
});

test("queued messages are acknowledged like sent messages", () => {
  const plan = buildZcodeCapturePlan(turns, {});
  const next = applyZcodeCaptureResult({}, plan, {
    sent: 0,
    queued: 2,
    failed: 0,
  });

  assert.equal(next.lastTurnId, "turn-001");
  assert.deepEqual(next.capturedTurnIds, [
    "turn-001:user",
    "turn-001:assistant",
  ]);
});

test("repeated prompt text clears only after the latest matching user is acknowledged", () => {
  const repeatedTurns = [
    { role: "user", content: "same question", turnId: "turn-001" },
    { role: "assistant", content: "first answer", turnId: "turn-001" },
    { role: "user", content: "same question", turnId: "turn-002" },
    { role: "assistant", content: "second answer", turnId: "turn-002" },
  ];
  const state = {
    capturedTurnIds: [],
    pendingPrompt: { prompt: "same question" },
  };
  const plan = buildZcodeCapturePlan(repeatedTurns, state);
  const partial = applyZcodeCaptureResult(state, plan, {
    sent: 2,
    queued: 0,
    failed: 2,
  });

  assert.equal(partial.lastTurnId, "turn-001");
  assert.deepEqual(partial.pendingPrompt, state.pendingPrompt);

  const retryPlan = buildZcodeCapturePlan(repeatedTurns.slice(2), partial);
  const complete = applyZcodeCaptureResult(partial, retryPlan, {
    sent: 2,
    queued: 0,
    failed: 0,
  });
  assert.equal(complete.lastTurnId, "turn-002");
  assert.equal(complete.pendingPrompt, null);
});
