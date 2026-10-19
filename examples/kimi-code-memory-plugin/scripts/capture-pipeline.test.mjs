/**
 * End-to-end capture pipeline test (DESIGN.md §5/§6):
 *
 *   fixture wire.jsonl → decoder → shared extractCaptureTurns → planCapture
 *   → buildMessagePayloads
 *
 * Everything short of the HTTP send is exercised, including the parts the
 * server will store: sanitized text, structured tool parts with names/status,
 * turn_id stamping, and the ack-set cursor.
 */

import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test, { after } from "node:test";

import { extractUnseenKimicodeTurns } from "./kimicode-transcript.mjs";
import { buildMessagePayloads, planCapture, turnDedupKey } from "./ov-session.mjs";
import { shapeKimicodeTurns } from "./capture-shape.mjs";

const cfg = { captureAssistantTurns: true, captureMaxLength: 24000 };

const FIXTURE_DIRS = [];
after(() => {
  for (const dir of FIXTURE_DIRS) rmSync(dir, { recursive: true, force: true });
});

function wireWith(rows) {
  const dir = mkdtempSync(join(tmpdir(), "kimicode-pipeline-"));
  FIXTURE_DIRS.push(dir);
  const path = join(dir, "wire.jsonl");
  writeFileSync(path, rows.map((row) => JSON.stringify(row)).join("\n") + "\n");
  return path;
}

const ROWS = [
  { type: "metadata", protocol_version: "1.5", created_at: 1 },
  {
    type: "context.append_message",
    agentId: "main",
    message: {
      role: "user",
      content: [{ type: "text", text: "帮我看下配置\n<openviking-context source=\"gateway-recall\">旧记忆不该被回采</openviking-context>" }],
      id: "msg_1",
      origin: { kind: "user" },
    },
  },
  { type: "context.append_loop_event", agentId: "main", event: { type: "content.part", turnId: "0", part: { type: "text", text: "我先读一下配置。" } } },
  { type: "context.append_loop_event", agentId: "main", event: { type: "tool.call", uuid: "u1", turnId: "0", toolCallId: "tc1", name: "Read", args: { path: "ov.conf" } } },
  { type: "context.append_loop_event", agentId: "main", event: { type: "tool.result", parentUuid: "u1", turnId: "0", toolCallId: "tc1", result: { output: "{\"a\":1}", durationMs: 8, note: "<system>12 lines read</system>" } } },
  { type: "context.append_loop_event", agentId: "main", event: { type: "tool.call", uuid: "u2", turnId: "0", toolCallId: "tc2", name: "Bash", args: { command: "false" } } },
  { type: "context.append_loop_event", agentId: "main", event: { type: "tool.result", parentUuid: "u2", turnId: "0", toolCallId: "tc2", result: { output: "command failed", durationMs: 3, isError: true } } },
  { type: "context.append_loop_event", agentId: "main", event: { type: "content.part", turnId: "0", part: { type: "text", text: "配置没问题。" } } },
  { type: "turn.ended", agentId: "main", turnId: 0, reason: "completed" },
];

test("pipeline shapes sanitized turns with structured tool parts", () => {
  const decoded = extractUnseenKimicodeTurns(wireWith(ROWS), null);
  assert.equal(decoded.status, "ok");

  const turns = shapeKimicodeTurns(decoded.entries, cfg);
  assert.ok(turns.length >= 4, `expected several turns, got ${turns.length}`);

  const userTurn = turns.find((t) => t.role === "user" && t.parts.some((p) => p.type === "text"));
  assert.match(userTurn.text, /帮我看下配置/);
  // The recalled block injected into the prompt must not be re-captured.
  assert.doesNotMatch(userTurn.text, /旧记忆不该被回采/);

  const assistantText = turns.filter((t) => t.role === "assistant" && t.parts.some((p) => p.type === "text"));
  assert.ok(assistantText.some((t) => /我先读一下配置/.test(t.text)));
  assert.ok(assistantText.some((t) => /配置没问题/.test(t.text)));

  const toolCalls = turns.flatMap((t) => t.parts.filter((p) => p.type === "tool" && p.tool_input !== undefined));
  const toolResults = turns.flatMap((t) => t.parts.filter((p) => p.type === "tool" && p.tool_output !== undefined));
  assert.equal(toolCalls.length, 2);
  assert.equal(toolResults.length, 2);

  const read = toolCalls.find((p) => p.tool_name === "Read");
  assert.equal(read.tool_id, "tc1");
  assert.deepEqual(read.tool_input, { path: "ov.conf" });
  const bashCall = toolCalls.find((p) => p.tool_name === "Bash");
  assert.equal(bashCall.tool_id, "tc2");

  // The failed result carries its output and an error status.
  const failed = toolResults.find((p) => p.tool_id === "tc2");
  assert.equal(failed.tool_status, "error");
  assert.match(failed.tool_output, /command failed/);
  const okResult = toolResults.find((p) => p.tool_id === "tc1");
  assert.equal(okResult.tool_status, "completed");
  assert.match(okResult.tool_output, /"a":1/);
  // The host display note never reaches the payload.
  assert.doesNotMatch(okResult.tool_output, /lines read/);

  // turn_id propagated through the shared extractor (§6.5).
  assert.ok(turns.every((t) => t.turnId === "0"));

  // Conversation order inside the entry stream: user first, tools in the
  // middle, assistant text closing the turn.
  const kinds = decoded.entries.map((e) => {
    const p = e.payload;
    if (p.type === "message") return `${p.role}-text`;
    return p.type === "function_call" ? "call" : "result";
  });
  assert.equal(kinds[0], "user-text");
  assert.equal(kinds[kinds.length - 1], "assistant-text");
  assert.ok(kinds.indexOf("call") < kinds.indexOf("result"));
});

test("pipeline cursor acks confirmed turns and resumes from the rest", () => {
  const decoded = extractUnseenKimicodeTurns(wireWith(ROWS), null);
  const turns = shapeKimicodeTurns(decoded.entries, cfg);

  const firstPlan = planCapture(turns, []);
  assert.equal(firstPlan.length, turns.length);
  const payloads = buildMessagePayloads(
    firstPlan.map((item) => item.turn),
    { peerId: "repo-a" },
  );
  assert.ok(payloads.every((p) => p.turn_id === "0"));
  assert.ok(payloads.every((p) => p.peer_id === "repo-a"));

  // After the server confirms everything, the next plan is empty.
  const acked = new Set(firstPlan.flatMap((item) => item.keys));
  const secondPlan = planCapture(turns, acked);
  assert.equal(secondPlan.length, 0);

  // A new turn appended to the wire is the only thing re-planned.
  const extended = [
    ...ROWS,
    { type: "turn.prompt", agentId: "main", input: [{ type: "text", text: "第二轮" }], origin: { kind: "user" }, turnId: 1 },
    { type: "context.append_loop_event", agentId: "main", event: { type: "content.part", turnId: "1", part: { type: "text", text: "第二轮回复" } } },
    { type: "turn.ended", agentId: "main", turnId: 1, reason: "completed" },
  ];
  const decoded2 = extractUnseenKimicodeTurns(wireWith(extended), null);
  const turns2 = shapeKimicodeTurns(decoded2.entries, cfg);
  const plan2 = planCapture(turns2, acked);
  assert.ok(plan2.every((item) => item.turn.turnId === "1"));
  assert.ok(turnDedupKey(plan2[0].turn).startsWith("1:"));
});
