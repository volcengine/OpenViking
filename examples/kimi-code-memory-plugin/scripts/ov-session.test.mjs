/**
 * Tests for ov-session.mjs: the ack-set cursor, payload shape and commit
 * policy (DESIGN.md §5.5/§6.5).
 */

import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  buildMessagePayloads,
  planCapture,
  sendPlannedCapture,
  shouldCommit,
  turnDedupKey,
} from "./ov-session.mjs";

test("dedup keys distinguish turns, roles and tool parts", () => {
  const textTurn = { role: "user", text: "hi", turnId: "0" };
  assert.equal(turnDedupKey(textTurn), "0:user");
  const assistantText = { role: "assistant", text: "done", turnId: "0" };
  assert.equal(turnDedupKey(assistantText), "0:assistant");
  const callTurn = {
    role: "assistant",
    turnId: "0",
    parts: [{ type: "tool", tool_id: "tc1", tool_name: "Bash", tool_input: { command: "ls" } }],
  };
  assert.equal(turnDedupKey(callTurn), "0:assistant:tc1:call");
  const resultTurn = {
    role: "user",
    turnId: "0",
    parts: [{ type: "tool", tool_id: "tc1", tool_name: "Bash", tool_output: "a.md" }],
  };
  assert.equal(turnDedupKey(resultTurn), "0:user:tc1:result");
  // Two calls in one turn never collide.
  const twoCalls = {
    role: "assistant",
    turnId: "1",
    parts: [
      { type: "tool", tool_id: "tc1", tool_input: {} },
      { type: "tool", tool_id: "tc2", tool_input: {} },
    ],
  };
  assert.equal(turnDedupKey(twoCalls), "1:assistant:tc1:call|1:assistant:tc2:call");
  // No turnId: content hash fallback, still role-scoped.
  const noTurn = { role: "user", text: "hi" };
  assert.notEqual(turnDedupKey(noTurn), turnDedupKey({ role: "assistant", text: "hi" }));
});

test("planCapture skips fully acked turns and re-sends partial ones", () => {
  const turns = [
    { role: "user", text: "a", turnId: "0" },
    { role: "assistant", text: "b", turnId: "0" },
    { role: "user", text: "c", turnId: "1" },
  ];
  const acked = new Set(["0:user", "0:assistant"]);
  const plan = planCapture(turns, acked);
  assert.equal(plan.length, 1);
  assert.equal(plan[0].turn.text, "c");

  // A multi-tool turn whose second call is unacked is re-sent whole.
  const partial = planCapture(
    [
      {
        role: "assistant",
        turnId: "2",
        parts: [
          { type: "tool", tool_id: "tc1", tool_input: {} },
          { type: "tool", tool_id: "tc2", tool_input: {} },
        ],
      },
    ],
    new Set(["2:assistant:tc1:call"]),
  );
  assert.equal(partial.length, 1);
  assert.deepEqual(partial[0].keys, ["2:assistant:tc1:call", "2:assistant:tc2:call"]);
});

test("colliding text keys in one turn stay distinct", () => {
  const first = { role: "assistant", text: "first", turnId: "7" };
  const second = { role: "assistant", text: "second", turnId: "7" };
  assert.equal(turnDedupKey(first), turnDedupKey(second));

  const plan = planCapture([first, second], new Set());
  assert.equal(plan.length, 2);
  assert.notEqual(plan[0].keys[0], plan[1].keys[0]);
  assert.equal(plan[0].keys[0], "7:assistant");
  // The suffix identifies the entry by its content, so it does not move when an
  // earlier collision disappears (an undone step, a repaired journal).
  assert.match(plan[1].keys[0], /^7:assistant#[0-9a-z]+$/);
  assert.equal(planCapture([first, second], new Set())[1].keys[0], plan[1].keys[0]);
  assert.notEqual(planCapture([first, { ...second, text: "third" }], new Set())[1].keys[0], plan[1].keys[0]);

  // Acking the first entry must not retire the second before it was sent.
  const replan = planCapture([first, second], new Set(["7:assistant"]));
  assert.equal(replan.length, 1);
  assert.deepEqual(replan[0].keys, [plan[1].keys[0]]);
});

test("payloads carry parts when present, content otherwise, plus ids", () => {
  const payloads = buildMessagePayloads(
    [
      { role: "user", text: "q", turnId: "7" },
      {
        role: "assistant",
        turnId: "7",
        parts: [
          { type: "text", text: "thinking out loud" },
          { type: "tool", tool_id: "tc1", tool_name: "Bash", tool_input: { command: "ls" }, tool_status: "running" },
        ],
      },
      {
        role: "user",
        turnId: "7",
        parts: [{ type: "tool", tool_id: "tc1", tool_name: "Bash", tool_output: "a.md", tool_status: "completed" }],
      },
    ],
    { peerId: "my-repo" },
  );
  assert.deepEqual(payloads[0], { role: "user", content: "q", turn_id: "7", peer_id: "my-repo" });
  assert.equal(payloads[1].parts.length, 2);
  assert.equal(payloads[1].parts[1].type, "tool");
  assert.equal(payloads[1].parts[1].tool_name, "Bash");
  assert.equal(payloads[2].parts[0].tool_output, "a.md");
  assert.ok(payloads.every((p) => p.turn_id === "7"));
});

test("commit policy follows the event, then the threshold", () => {
  assert.equal(shouldCommit("interrupt", {}, 0, 1), true);
  assert.equal(shouldCommit("session-end", {}, 0, 1), true);
  assert.equal(shouldCommit("pre-compact", {}, 0, 1), true);
  assert.equal(shouldCommit("pre-compact", { autoCommitOnCompact: false }, 0, 1), false);
  assert.equal(shouldCommit("stop", { commitTurnThreshold: 8 }, 0, 7), false);
  assert.equal(shouldCommit("stop", { commitTurnThreshold: 8 }, 0, 8), true);
  assert.equal(shouldCommit("stop", { commitTurnThreshold: 8 }, 5, 3), true);
  assert.equal(shouldCommit("stop", {}, 0, 1), false); // default threshold is 8
  assert.equal(shouldCommit("stop", {}, 0, 0), false); // nothing captured
});

test("a partial send acknowledges only what the server accepted", async () => {
  const previous = process.env.OPENVIKING_PENDING_DIR;
  const dir = await mkdtemp(join(tmpdir(), "kimi-send-"));
  process.env.OPENVIKING_PENDING_DIR = dir;
  try {
    const acked = [];
    const plan = [
      { turn: { role: "user", text: "a", turnId: "0" }, keys: ["0:user"] },
      { turn: { role: "assistant", text: "b", turnId: "0" }, keys: ["0:assistant"] },
      { turn: { role: "user", text: "c", turnId: "1" }, keys: ["1:user"] },
    ];
    const result = await sendPlannedCapture(
      async (path, init = {}) => {
        if (path.endsWith("/messages/batch")) return { ok: false, status: 404, error: { message: "no batch route" } };
        const body = JSON.parse(init.body || "{}");
        if (body.content === "c") return { ok: false, status: 422, error: { message: "rejected" } };
        return { ok: true, status: 200, result: { ok: true } };
      },
      "kc-partial",
      plan,
      { onSent: async ({ confirmed }) => acked.push(...confirmed) },
    );

    // The rejected payload must not acknowledge its neighbours, and the turn it
    // belongs to must stay out of the ack set so the cursor cannot advance past it.
    assert.deepEqual(acked, ["0:user", "0:assistant"]);
    assert.equal(result.sent, 2);
    assert.equal(result.failed, 1);
  } finally {
    if (previous === undefined) delete process.env.OPENVIKING_PENDING_DIR;
    else process.env.OPENVIKING_PENDING_DIR = previous;
    await rm(dir, { recursive: true, force: true });
  }
});
