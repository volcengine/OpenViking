/**
 * The projector: active-branch geometry and the turn/step state machine.
 *
 * Every test states a host behaviour and asserts the conversation that comes
 * out of it, because that conversation is what the server will remember.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { activeChain, parseWireTree, projectWire, turnHasContent } from "../../scripts/wire-projector.mjs";
import {
  appendMessage,
  contentPart,
  contextApplyCompaction,
  contextClear,
  contextUndo,
  contextUndone,
  forked,
  metadata,
  resetClock,
  simpleTurn,
  stepBegin,
  stepEnd,
  switched,
  toolCall,
  toolResult,
  turnCancel,
  turnEnded,
  turnPrompt,
  turnSteer,
} from "../fixtures/wire-fixtures.mjs";

function entries(records) {
  return records.map((record, index) => ({ line: index + 1, record }));
}

function project(records) {
  return projectWire(entries(records));
}

function assistantText(turn) {
  return turn.segments
    .flatMap((segment) => segment.messages)
    .flatMap((message) => message.parts)
    .filter((part) => part.type === "text")
    .map((part) => part.text);
}

test("a plain turn becomes one user segment with ordered assistant text", () => {
  resetClock();
  const projection = project([
    metadata(),
    turnPrompt({ turnId: 0, text: "how do I build this?" }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "First, run " } }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "the tests." } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);

  assert.equal(projection.status, "ok");
  assert.equal(projection.turns.length, 1);
  const [turn] = projection.turns;
  assert.equal(turn.complete, true);
  assert.equal(turn.segments.length, 1);
  assert.equal(turn.segments[0].userText, "how do I build this?");
  assert.deepEqual(assistantText(turn), ["First, run ", "the tests."]);
});

test("think parts are not conversation, text parts are", () => {
  const projection = project([
    metadata(),
    turnPrompt({ turnId: 0 }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "think", think: "the user probably means X" } }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "visible answer" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  assert.deepEqual(assistantText(projection.turns[0]), ["visible answer"]);
});

test("a tool call and its result are linked by toolCallId, and a missing result is not invented", () => {
  const projection = project([
    metadata(),
    turnPrompt({ turnId: 0 }),
    stepBegin({ turnId: 0, step: 1 }),
    toolCall({ turnId: 0, step: 1, toolCallId: "call-a", name: "Read", args: { file_path: "/tmp/a" } }),
    toolCall({ turnId: 0, step: 1, toolCallId: "call-b", name: "Grep", args: { pattern: "x" } }),
    stepEnd({ turnId: 0, step: 1 }),
    stepBegin({ turnId: 0, step: 2 }),
    toolResult({ turnId: 0, toolCallId: "call-a", output: "contents of a" }),
    stepEnd({ turnId: 0, step: 2 }),
    turnEnded({ turnId: 0 }),
  ]);

  const [turn] = projection.turns;
  const calls = turn.segments[0].messages.flatMap((message) => message.parts).filter((part) => part.type === "tool");
  assert.deepEqual(calls.map((part) => part.tool_id), ["call-a", "call-b"]);
  assert.deepEqual(calls.map((part) => part.tool_status), ["running", "running"]);
  assert.deepEqual(turn.segments[0].toolResults.map((result) => result.tool_id), ["call-a"]);
  assert.equal(turn.segments[0].toolResults[0].tool_output, "contents of a");
  // The missing result for call-b is counted, never synthesized.
  assert.equal(turn.segments[0].toolResults.length, 1);
});

test("a tool result for a call that never happened is counted, not turned into a message", () => {
  const projection = project([
    metadata(),
    turnPrompt({ turnId: 0 }),
    stepBegin({ turnId: 0, step: 1 }),
    toolResult({ turnId: 0, toolCallId: "ghost", output: "orphan" }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  assert.deepEqual(projection.turns[0].segments[0].toolResults, []);
  assert.equal(projection.diagnostics.orphanToolResults, 1);
});

test("content parts only count for the step that is open", () => {
  const projection = project([
    metadata(),
    turnPrompt({ turnId: 0 }),
    stepBegin({ turnId: 0, step: 1, uuid: "u1" }),
    contentPart({ turnId: 0, step: 1, uuid: "u2", part: { type: "text", text: "wrong step" } }),
    contentPart({ turnId: 0, step: 1, uuid: "u1", part: { type: "text", text: "right step" } }),
    stepEnd({ turnId: 0, step: 1, uuid: "u1" }),
    turnEnded({ turnId: 0 }),
  ]);
  assert.deepEqual(assistantText(projection.turns[0]), ["right step"]);
});

test("a steer closes the current turn and opens a new one, as the host does", () => {
  // The host's `normalizeLegacyTurnSteerRecords` turns a steer into
  // "end the current logical turn as completed, start a new one with a new id",
  // and maps the wire's source turnId to the new logical turn. So the projection
  // must show two turns, not one turn with two segments.
  const projection = project([
    metadata(),
    turnPrompt({ turnId: 0, text: "start here", promptId: "p0" }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "working on it" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnSteer({ turnId: 0, messageId: "steer-1", text: "actually do this instead" }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "adjusted" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);

  assert.equal(projection.turns.length, 2);
  const [first, second] = projection.turns;
  assert.equal(first.segments[0].userText, "start here");
  assert.equal(first.ended.reason, "completed", "a steer closes the previous turn normally");
  assert.equal(second.segments[0].userText, "actually do this instead");
  assert.equal(second.segments[0].messageId, "steer-1");
  assert.equal(second.ended.reason, "completed");
  // Each logical turn gets its own id, and they are distinct.
  assert.notEqual(first.turnId, second.turnId);
  assert.deepEqual(assistantText(first), ["working on it"]);
  assert.deepEqual(assistantText(second), ["adjusted"]);
});

test("turn.ended after a steer closes the steered turn, not the original", () => {
  // The wire keeps one source turnId, so a `turn.ended` naming it must resolve to
  // whichever logical turn currently occupies it — the steered one.
  const projection = project([
    metadata(),
    turnPrompt({ turnId: 7, text: "first" }),
    turnSteer({ turnId: 7, messageId: "s7", text: "second" }),
    stepBegin({ turnId: 7, step: 1 }),
    contentPart({ turnId: 7, step: 1, part: { type: "text", text: "answer to the steer" } }),
    stepEnd({ turnId: 7, step: 1 }),
    turnEnded({ turnId: 7, reason: "completed" }),
  ]);
  assert.equal(projection.turns.length, 2);
  assert.equal(projection.turns[1].open, false);
  assert.deepEqual(assistantText(projection.turns[1]), ["answer to the steer"]);
  assert.deepEqual(assistantText(projection.turns[0]), []);
});

test("a cancelled turn id is skipped by the clock, so ids are never reused", () => {
  const projection = project([
    metadata(),
    turnPrompt({ turnId: 0, text: "one" }),
    turnEnded({ turnId: 0 }),
    turnCancel({ turnId: 1, target: "queued" }),
    turnPrompt({ text: "two" }),
    turnEnded({}),
  ]);
  const ids = projection.turns.map((turn) => turn.turnId);
  assert.equal(ids[0], 0);
  assert.equal(ids.includes(1), false, "a cancelled id is not handed to a later turn");
});

test("a steer with no current turn opens one instead of being dropped", () => {
  const projection = project([
    metadata(),
    turnSteer({ turnId: 3, messageId: "steer-only", text: "only input" }),
    stepBegin({ turnId: 3, step: 1 }),
    contentPart({ turnId: 3, step: 1, part: { type: "text", text: "answer" } }),
    stepEnd({ turnId: 3, step: 1 }),
    turnEnded({ turnId: 3 }),
  ]);
  assert.equal(projection.turns.length, 1);
  assert.equal(projection.turns[0].segments[0].userText, "only input");
});

test("turn.cancel is not a terminal state", () => {
  const projection = project([
    metadata(),
    turnPrompt({ turnId: 0 }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "partial" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnCancel({ turnId: 0, target: "user" }),
  ]);
  const [turn] = projection.turns;
  assert.equal(turn.open, true);
  assert.equal(turn.terminal, false);
  assert.equal(turn.cancelRequested, true);
});

test("a failed or cancelled turn is terminal but partial", () => {
  for (const reason of ["failed", "cancelled", "aborted"]) {
    const projection = project([
      metadata(),
      turnPrompt({ turnId: 0 }),
      stepBegin({ turnId: 0, step: 1 }),
      contentPart({ turnId: 0, step: 1, part: { type: "text", text: "some work" } }),
      stepEnd({ turnId: 0, step: 1 }),
      turnEnded({ turnId: 0, reason }),
    ]);
    const [turn] = projection.turns;
    assert.equal(turn.complete, false, reason);
    assert.equal(turn.terminal, true, reason);
    assert.equal(turn.terminalPartial, true, reason);
    assert.deepEqual(assistantText(turn), ["some work"], reason);
  }
});

test("an open step makes the turn incomplete even when it ended completed", () => {
  const projection = project([
    metadata(),
    turnPrompt({ turnId: 0 }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "still writing" } }),
    turnEnded({ turnId: 0, reason: "completed" }),
  ]);
  const [turn] = projection.turns;
  assert.equal(turn.openSteps, true);
  assert.equal(turn.complete, false);
  // The content is still there: partial capture keeps what exists.
  assert.deepEqual(assistantText(turn), ["still writing"]);
});

test("a single branch switch keeps the base prefix and the new segment", () => {
  // 1 metadata, 2 prompt, 3 step, 4 text, 5 step.end, 6 switch, 7 prompt...
  const records = [
    metadata(),
    turnPrompt({ turnId: 0, text: "original question" }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "original answer" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
    switched({ branch: "undo-1", baseLine: 6 }),
    turnPrompt({ turnId: 1, text: "replacement question" }),
    stepBegin({ turnId: 1, step: 1 }),
    contentPart({ turnId: 1, step: 1, part: { type: "text", text: "replacement answer" } }),
    stepEnd({ turnId: 1, step: 1 }),
    turnEnded({ turnId: 1 }),
  ];
  const projection = project(records);
  assert.equal(projection.activeBranch, "undo-1");
  assert.deepEqual(projection.turns.map((turn) => turn.segments[0].userText), [
    "original question",
    "replacement question",
  ]);
  // The switch record itself never becomes a message.
  assert.equal(projection.turns.length, 2);
  // The base prefix has to keep the whole first turn, including its
  // `turn.ended`: a base line that cuts the ending off leaves a turn that
  // looks open forever, and a later boundary can never commit it.
  assert.equal(projection.turns[0].complete, true);
  assert.equal(projection.turns[0].terminal, true);
});

test("a discarded middle segment is not in the active chain", () => {
  // main: prompt A (2) ... switch to b at 5, b has prompt B, then switch to c
  // whose base is main at line 5 — so b is abandoned.
  const records = [
    metadata(),                                  // 1
    turnPrompt({ turnId: 0, text: "A" }),        // 2
    stepBegin({ turnId: 0, step: 1 }),           // 3
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "answer A" } }), // 4
    stepEnd({ turnId: 0, step: 1 }),             // 5
    switched({ branch: "b", baseLine: 5 }),      // 6
    turnPrompt({ turnId: 1, text: "B" }),        // 7
    stepBegin({ turnId: 1, step: 1 }),           // 8
    contentPart({ turnId: 1, step: 1, part: { type: "text", text: "answer B" } }), // 9
    stepEnd({ turnId: 1, step: 1 }),             // 10
    turnEnded({ turnId: 1 }),                    // 11
    switched({ branch: "c", baseBranch: "main", baseLine: 5 }), // 12
    turnPrompt({ turnId: 2, text: "C" }),        // 13
    stepBegin({ turnId: 2, step: 1 }),           // 14
    contentPart({ turnId: 2, step: 1, part: { type: "text", text: "answer C" } }), // 15
    stepEnd({ turnId: 2, step: 1 }),             // 16
    turnEnded({ turnId: 2 }),                    // 17
  ];
  const projection = project(records);
  assert.equal(projection.activeBranch, "c");
  assert.deepEqual(projection.turns.map((turn) => turn.segments[0].userText), ["A", "C"]);
  assert.ok(!projection.turns.some((turn) => turn.segments[0].userText === "B"));
});

test("a multi-level switch follows the base chain recursively", () => {
  // main → d (base main:4) → e (base d:8): the chain is main[1..4] + d[5..8] + e[9..]
  const records = [
    metadata(),                                       // 1
    turnPrompt({ turnId: 0, text: "main q" }),        // 2
    stepBegin({ turnId: 0, step: 1 }),                // 3
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "main a" } }), // 4
    stepEnd({ turnId: 0, step: 1 }),                  // 5
    switched({ branch: "d", baseLine: 4 }),           // 6
    turnPrompt({ turnId: 1, text: "d q" }),           // 7
    stepBegin({ turnId: 1, step: 1 }),                // 8
    contentPart({ turnId: 1, step: 1, part: { type: "text", text: "d a" } }), // 9
    stepEnd({ turnId: 1, step: 1 }),                  // 10
    turnEnded({ turnId: 1 }),                         // 11
    switched({ branch: "e", baseBranch: "d", baseLine: 10 }), // 12
    turnPrompt({ turnId: 2, text: "e q" }),           // 13
    stepBegin({ turnId: 2, step: 1 }),                // 14
    contentPart({ turnId: 2, step: 1, part: { type: "text", text: "e a" } }), // 15
    stepEnd({ turnId: 2, step: 1 }),                  // 16
    turnEnded({ turnId: 2 }),                         // 17
  ];
  const projection = project(records);
  assert.equal(projection.activeBranch, "e");
  assert.deepEqual(projection.turns.map((turn) => turn.segments[0].userText), [
    "main q",
    "d q",
    "e q",
  ]);
});

test("an unknown base branch stops the projection instead of guessing", () => {
  const projection = project([
    metadata(),
    turnPrompt({ turnId: 0 }),
    switched({ branch: "b", baseBranch: "nowhere", baseLine: 2 }),
    turnPrompt({ turnId: 1 }),
  ]);
  assert.equal(projection.status, "error");
  assert.match(projection.error, /unknown branch/);
  assert.deepEqual(projection.turns, []);
});

test("a base chain cycle is reported, not silently resolved", () => {
  const records = [
    metadata(),
    turnPrompt({ turnId: 0 }),
    switched({ branch: "a", baseLine: 2 }),
    switched({ branch: "b", baseBranch: "a", baseLine: 3 }),
    switched({ branch: "a", baseBranch: "b", baseLine: 4 }),
  ];
  const projection = project(records);
  assert.equal(projection.status, "error");
  assert.match(projection.error, /cycles/);
});

test("a malformed switch is skipped and counted", () => {
  const projection = project([
    metadata(),
    turnPrompt({ turnId: 0 }),
    { type: "agent.switched", agentId: "main", branch: "x" },
    turnEnded({ turnId: 0 }),
  ]);
  assert.equal(projection.status, "ok");
  assert.equal(projection.diagnostics.malformedSwitches, 1);
});

test("parseWireTree and activeChain expose the host's segment geometry", () => {
  const records = [
    metadata(),
    turnPrompt({ turnId: 0 }),
    switched({ branch: "b", baseLine: 2 }),
    turnPrompt({ turnId: 1 }),
  ];
  const list = entries(records);
  const tree = parseWireTree(list, list.length);
  assert.deepEqual(tree.segments.map((segment) => [segment.branch, segment.fromLine, segment.toLine]), [
    ["main", 1, 3],
    ["b", 4, 4],
  ]);
  const chain = activeChain(list, tree);
  assert.deepEqual(chain.map((item) => item.line), [1, 2, 4]);
});

test("a paired legacyUndoLine means the undo was already expressed by the switch", () => {
  const projection = project([
    metadata(),
    turnPrompt({ turnId: 0 }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "a" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
    contextUndo({ count: 1 }),                        // line 7
    switched({ branch: "b", baseLine: 6, legacyUndoLine: 7 }),
    turnPrompt({ turnId: 1 }),
  ]);
  assert.equal(projection.status, "ok");
  assert.equal(projection.diagnostics.undoneTurns, 0);
});

test("an unpaired context.undo is counted as an undo anchor", () => {
  const projection = project([
    metadata(),
    turnPrompt({ turnId: 0 }),
    turnEnded({ turnId: 0 }),
    contextUndo({ count: 2 }),
  ]);
  assert.equal(projection.diagnostics.undoneTurns, 2);
});

test("context.clear drops open local turns and keeps delivered ones", () => {
  const projection = project([
    metadata(),
    turnPrompt({ turnId: 0, text: "delivered" }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "answer" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
    turnPrompt({ turnId: 1, text: "not delivered yet" }),
    contextClear(),
  ]);
  assert.deepEqual(projection.turns.map((turn) => turn.segments[0].userText), ["delivered"]);
  assert.equal(projection.diagnostics.clearedTurns, 1);
});

test("a compaction summary is not new conversation", () => {
  const projection = project([
    metadata(),
    turnPrompt({ turnId: 0, text: "before" }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "answer" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
    contextApplyCompaction({ summary: "a condensed history" }),
  ]);
  assert.equal(projection.turns.length, 1);
  assert.equal(projection.diagnostics.compactions, 1);
});

test("context.append_message never becomes a captured message", () => {
  const projection = project([
    metadata(),
    turnPrompt({ turnId: 0, text: "real question" }),
    appendMessage({ role: "user", text: "<injected context>", origin: { kind: "injection" } }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "answer" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  assert.deepEqual(projection.turns[0].segments.map((segment) => segment.userText), ["real question"]);
});

test("the forked marker produces no message", () => {
  const projection = project([
    metadata(),
    forked(),
    turnPrompt({ turnId: 0, text: "after the fork" }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "answer" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  assert.equal(projection.turns.length, 1);
  assert.equal(projection.turns[0].segments[0].userText, "after the fork");
});

test("an unknown record type is ignored without becoming a message", () => {
  const projection = project([
    metadata(),
    { type: "usage.updated", agentId: "main", tokens: 12 },
    turnPrompt({ turnId: 0, text: "q" }),
    { type: "goal.updated", agentId: "main", status: "active" },
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "a" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  assert.equal(projection.turns.length, 1);
  assert.deepEqual(assistantText(projection.turns[0]), ["a"]);
});

test("turnHasContent distinguishes an empty turn from a silent one", () => {
  const withText = project([metadata(), ...simpleTurn(0)]);
  assert.equal(turnHasContent(withText.turns[0]), true);

  const empty = project([metadata(), turnPrompt({ turnId: 0, text: "" }), turnEnded({ turnId: 0 })]);
  assert.equal(turnHasContent(empty.turns[0]), false);
});
