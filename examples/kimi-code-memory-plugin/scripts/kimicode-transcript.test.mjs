/**
 * Tests for kimicode-transcript.mjs.
 *
 * Fixtures are synthesized from the record shapes verified against Kimi Code
 * CLI 2.2.0 (see DESIGN.md §5 evidence): the wire manifest's closed loop-event
 * set, the 12-kind origin taxonomy, and tool.call/tool.result pairing — the
 * result carries `parentUuid` + `toolCallId` and NO turnId, which is what the
 * real 90k-line session wire does and what the first draft got wrong.
 */

import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { extractUnseenKimicodeTurns } from "./kimicode-transcript.mjs";
import { extractCaptureTurns } from "./shared/capture-utils.mjs";

function wireWith(rows) {
  const dir = mkdtempSync(join(tmpdir(), "kimicode-wire-"));
  const path = join(dir, "wire.jsonl");
  writeFileSync(path, rows.map((row) => JSON.stringify(row)).join("\n") + "\n");
  return path;
}

const prompt = (turnId, text) => [
  { type: "turn.prompt", agentId: "main", input: [{ type: "text", text }], origin: { kind: "user" }, promptId: `msg_${turnId}`, turnId },
  { type: "context.append_message", agentId: "main", message: { role: "user", content: [{ type: "text", text }], id: `msg_${turnId}`, origin: { kind: "user" } } },
];

const BASE_ROWS = [
  { type: "metadata", protocol_version: "1.5", created_at: 1 },
  ...prompt(0, "修复发布计划"),
  // Noise origins that must never become the captured prompt (#5673 class):
  { type: "context.append_message", agentId: "main", message: { role: "user", content: [{ type: "text", text: "<skill-loaded>…</skill-loaded>" }], id: "msg_2", origin: { kind: "skill_activation" } } },
  { type: "context.append_message", agentId: "main", message: { role: "user", content: [{ type: "text", text: "<hook_result>…</hook_result>" }], id: "msg_3", origin: { kind: "hook_result" } } },
  { type: "context.append_loop_event", agentId: "main", event: { type: "step.begin", uuid: "s1", turnId: "0", step: 1, stepUuid: "s1" } },
  { type: "context.append_loop_event", agentId: "main", event: { type: "content.part", uuid: "p1", turnId: "0", step: 1, stepUuid: "s1", part: { type: "text", text: "好的，我先看一下。" } } },
  { type: "context.append_loop_event", agentId: "main", event: { type: "content.part", uuid: "p2", turnId: "0", step: 1, stepUuid: "s1", part: { type: "think", think: "内部思考不应被采集" } } },
  { type: "context.append_loop_event", agentId: "main", event: { type: "tool.call", uuid: "u1", turnId: "0", step: 1, stepUuid: "s1", toolCallId: "tc1", name: "Bash", args: { command: "ls" } } },
  // Real shape: no turnId anywhere on the result (§6.1).
  { type: "context.append_loop_event", agentId: "main", event: { type: "tool.result", parentUuid: "u1", toolCallId: "tc1", result: { output: "a.md", durationMs: 12 } } },
  { type: "context.append_loop_event", agentId: "main", event: { type: "step.end", uuid: "s1", turnId: "0", step: 1, stepUuid: "s1" } },
  { type: "context.append_loop_event", agentId: "main", event: { type: "step.begin", uuid: "s2", turnId: "0", step: 2, stepUuid: "s2" } },
  { type: "context.append_loop_event", agentId: "main", event: { type: "tool.call", uuid: "u2", turnId: "0", step: 2, stepUuid: "s2", toolCallId: "tc2", name: "Read", args: { path: "x.md" } } },
  { type: "context.append_loop_event", agentId: "main", event: { type: "tool.result", parentUuid: "u2", toolCallId: "tc2", result: { output: "boom", durationMs: 0, isError: true } } },
  { type: "context.append_loop_event", agentId: "main", event: { type: "content.part", uuid: "p3", turnId: "0", step: 3, stepUuid: "s3", part: { type: "text", text: "已完成。" } } },
  { type: "turn.ended", agentId: "main", turnId: 0, reason: "completed" },
];

test("decodes user text, assistant text and tool entries", () => {
  const r = extractUnseenKimicodeTurns(wireWith(BASE_ROWS), null);
  assert.equal(r.status, "ok");
  assert.equal(r.protocolVersion, "1.5");
  assert.equal(r.unresolvedResults, 0);
  assert.equal(r.lateResults, 0);
  const user = r.turns.find((t) => t.role === "user");
  assert.match(user.content, /修复发布计划/);
  assert.doesNotMatch(user.content, /skill-loaded|hook_result/);
  const assistant = r.turns.filter((t) => t.role === "assistant").map((t) => t.content).join("\n");
  assert.match(assistant, /好的，我先看一下/);
  assert.match(assistant, /已完成/);
  assert.doesNotMatch(assistant, /内部思考/);

  const shape = r.entries.map((e) => `${e.payload.type}:${e.payload.role || ""}`);
  assert.deepEqual(shape, [
    "message:user",
    "message:assistant",
    "function_call_output:",
    "message:assistant",
    "function_call_output:",
    "message:assistant",
  ]);
  // Step 1 merges its text part and its call into one assistant message (§6.3).
  const first = r.entries[1].payload;
  assert.equal(first.role, "assistant");
  assert.deepEqual(first.content, [
    { type: "text", text: "好的，我先看一下。" },
    { type: "tool_call", call_id: "tc1", name: "Bash", arguments: { command: "ls" } },
  ]);
  const results = r.entries.filter((e) => e.payload.type === "function_call_output");
  assert.equal(results.length, 2);
  assert.equal(results[0].payload.turn_id, "0");
  assert.equal(results[0].payload.call_id, "tc1");
  assert.equal(results[0].payload.output, "a.md");
  assert.equal(results[0].payload.duration_ms, 12);
  assert.equal(results[1].payload.isError, true);
  assert.equal(r.unknownTypes.length, 0);
});

test("tool results land on the turn that issued the call, not the next one", () => {
  // The regression that mattered: tool.result has no turnId, so a positional
  // fallback shifts every result onto turn N+1 and lets a mid-turn host
  // notification overwrite the user's prompt.
  const rows = [
    ...prompt(0, "第一问"),
    { type: "context.append_loop_event", agentId: "main", event: { type: "tool.call", uuid: "u1", turnId: "0", step: 1, stepUuid: "s1", toolCallId: "tc1", name: "Bash", args: { command: "ls" } } },
    { type: "context.append_loop_event", agentId: "main", event: { type: "tool.result", parentUuid: "u1", toolCallId: "tc1", result: { output: "第一问的结果", durationMs: 3 } } },
    { type: "turn.ended", agentId: "main", turnId: 0, reason: "completed" },
    ...prompt(1, "第二问"),
    // A background-task notification arrives after the prompt, inside turn 1.
    { type: "context.append_message", agentId: "main", message: { role: "user", content: [{ type: "text", text: "<notification id=\"task:x:completed\">Title: Background process completed</notification>" }], id: "msg_n", origin: { kind: "task" } } },
    { type: "context.append_loop_event", agentId: "main", event: { type: "tool.call", uuid: "u2", turnId: "1", step: 1, stepUuid: "s2", toolCallId: "tc2", name: "Bash", args: { command: "pwd" } } },
    { type: "context.append_loop_event", agentId: "main", event: { type: "tool.result", parentUuid: "u2", toolCallId: "tc2", result: { output: "第二问的结果", durationMs: 4 } } },
    { type: "turn.ended", agentId: "main", turnId: 1, reason: "completed" },
  ];
  const r = extractUnseenKimicodeTurns(wireWith(rows), null);
  const byTurn = (turnId) => r.entries.filter((e) => e.payload.turn_id === turnId);
  assert.equal(byTurn("0").filter((e) => e.payload.type === "function_call_output").length, 1);
  assert.equal(byTurn("1").filter((e) => e.payload.type === "function_call_output").length, 1);
  assert.equal(byTurn("0").filter((e) => e.payload.type === "function_call_output")[0].payload.output, "第一问的结果");
  assert.equal(byTurn("1").filter((e) => e.payload.type === "function_call_output")[0].payload.output, "第二问的结果");
  // The user's own words survive the mid-turn notification; the decoder hands
  // both through and the shared sanitizer strips the wrapper downstream.
  const turn1User = byTurn("1").find((e) => e.payload.role === "user" && e.payload.type === "message");
  assert.match(turn1User.payload.content[0].text, /第二问/);
  assert.match(turn1User.payload.content[0].text, /Background process completed/);
  const shaped = extractCaptureTurns(byTurn("1"), { captureAssistantTurns: true });
  const shapedText = shaped.map((t) => t.text).join("\n");
  assert.match(shapedText, /第二问/);
  assert.match(shapedText, /Background process completed/);
  assert.doesNotMatch(shapedText, /<notification/);
});

test("a result whose call is missing is skipped, never guessed onto a turn", () => {
  const rows = [
    ...prompt(0, "唯一回合"),
    { type: "context.append_loop_event", agentId: "main", event: { type: "tool.result", parentUuid: "ghost", toolCallId: "tc-x", result: { output: "孤立结果", durationMs: 1 } } },
    { type: "turn.ended", agentId: "main", turnId: 0, reason: "completed" },
  ];
  const r = extractUnseenKimicodeTurns(wireWith(rows), null);
  assert.equal(r.unresolvedResults, 1);
  assert.equal(r.entries.filter((e) => e.payload.type === "function_call_output").length, 0);
});

test("an unclosed turn at the tail of the wire emits nothing", () => {
  const rows = [
    ...prompt(0, "已结束"),
    { type: "context.append_loop_event", agentId: "main", event: { type: "content.part", uuid: "p1", turnId: "0", step: 1, stepUuid: "s1", part: { type: "text", text: "回复" } } },
    { type: "turn.ended", agentId: "main", turnId: 0, reason: "completed" },
    ...prompt(1, "还在跑"),
    { type: "context.append_loop_event", agentId: "main", event: { type: "content.part", uuid: "p2", turnId: "1", step: 1, stepUuid: "s2", part: { type: "text", text: "半句话" } } },
  ];
  const r = extractUnseenKimicodeTurns(wireWith(rows), null);
  assert.ok(r.entries.every((e) => e.payload.turn_id === "0"));
  assert.ok(r.turns.some((t) => /已结束/.test(t.content)));
  assert.ok(!r.turns.some((t) => /还在跑/.test(t.content)));
});

test("incremental decode resumes after lastTurnId", () => {
  const rows = [
    ...BASE_ROWS,
    ...prompt(1, "第二轮"),
    { type: "context.append_loop_event", agentId: "main", event: { type: "content.part", uuid: "p9", turnId: "1", step: 1, stepUuid: "s9", part: { type: "text", text: "第二轮回复" } } },
    { type: "turn.ended", agentId: "main", turnId: 1, reason: "completed" },
  ];
  const path = wireWith(rows);
  const r = extractUnseenKimicodeTurns(path, "0");
  assert.ok(r.turns.every((t) => t.turnId === "1"));
  // The cursor must bound the entries too, or every hook reshapes the whole
  // session just to have planCapture filter it back out.
  assert.ok(r.entries.length > 0);
  assert.ok(r.entries.every((entry) => entry.payload.turn_id === "1"));
  assert.ok(r.turns.some((t) => /第二轮/.test(t.content)));
});

test("system-origin prompts are captured and marked, not dropped", () => {
  const rows = [
    { type: "context.append_message", agentId: "main", message: { role: "user", content: [{ type: "text", text: "goal 续跑指令" }], id: "msg_g", origin: { kind: "system_trigger" } } },
    { type: "context.append_loop_event", agentId: "main", event: { type: "content.part", uuid: "p1", turnId: "0", step: 1, stepUuid: "s1", part: { type: "text", text: "继续" } } },
    { type: "turn.ended", agentId: "main", turnId: 0, reason: "completed" },
  ];
  const r = extractUnseenKimicodeTurns(wireWith(rows), null);
  assert.ok(r.turns.some((t) => t.role === "user" && /goal 续跑指令/.test(t.content)));
});

test("missing wire reports missing, not ok", () => {
  const r = extractUnseenKimicodeTurns(join(tmpdir(), "no-such-wire.jsonl"), null);
  assert.equal(r.status, "missing");
});

test("undone branch records are not captured", () => {
  // Realistic journal shape (kimi-code names new branches b1, b2, … and never
  // switches back to "main"): main holds turn 0, then undo creates b1 based
  // on main line 4 (keeping turn 0), b1 writes a turn the user then undoes by
  // creating b2 based on the same line. Only main's turn 0 and b2's turn may
  // be captured; b1's turn was retracted.
  const rows = [
    { type: "metadata", protocol_version: "1.5", created_at: 1 },
    ...prompt(0, "第一回合"),
    { type: "context.append_loop_event", agentId: "main", event: { type: "content.part", uuid: "p1", turnId: "0", step: 1, stepUuid: "s1", part: { type: "text", text: "回复一" } } },
    { type: "turn.ended", agentId: "main", turnId: 0, reason: "completed" },
    { type: "agent.switched", branch: "b1", base: { branch: "main", line: 4 }, reason: "undo" },
    ...prompt(1, "第二回合（作废）"),
    { type: "context.append_loop_event", agentId: "main", event: { type: "content.part", uuid: "p2", turnId: "1", step: 1, stepUuid: "s2", part: { type: "text", text: "回复二（作废）" } } },
    { type: "turn.ended", agentId: "main", turnId: 1, reason: "completed" },
    { type: "agent.switched", branch: "b2", base: { branch: "main", line: 4 }, reason: "undo" },
    ...prompt(1, "第二回合（最终版）"),
    { type: "context.append_loop_event", agentId: "main", event: { type: "content.part", uuid: "p3", turnId: "1", step: 1, stepUuid: "s3", part: { type: "text", text: "回复二（最终版）" } } },
    { type: "turn.ended", agentId: "main", turnId: 1, reason: "completed" },
  ];
  const r = extractUnseenKimicodeTurns(wireWith(rows), null);
  assert.equal(r.status, "ok");
  assert.equal(r.activeBranch, "b2");
  const all = r.turns.map((t) => t.content).join("\n");
  // History before the fork point survives.
  assert.match(all, /第一回合/);
  assert.match(all, /回复一/);
  // The active branch's turn is captured…
  assert.match(all, /第二回合（最终版）/);
  assert.match(all, /回复二（最终版）/);
  // …and the abandoned b1 attempt is not.
  assert.doesNotMatch(all, /作废/);
});

test("corrupt switch tree skips the round instead of guessing", () => {
  const rows = [
    { type: "metadata", protocol_version: "1.5", created_at: 1 },
    { type: "agent.switched", branch: "bX", base: { branch: "ghost", line: 1 } },
    ...prompt(0, "x"),
  ];
  const r = extractUnseenKimicodeTurns(wireWith(rows), null);
  assert.equal(r.status, "tree_error");
  assert.equal(r.entries.length, 0);
});
