/**
 * Decoder tests for the Qoder CLI transcript, built on sanitized copies of real
 * Qoder CLI 1.1.88 transcripts (tests/fixtures/qoder/).
 */

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  buildQoderTurns,
  extractQoderTurns,
  extractUnseenQoderTurns,
  parseTranscript,
} from "../hosts/qoder-turns.mjs";
import { buildIncrementalCapturePlan } from "../hosts/incremental-turn-capture.mjs";

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), "fixtures", "qoder");
const fixture = (name) => join(FIXTURES, `${name}.jsonl`);
const records = (name) => parseTranscript(readFileSync(fixture(name), "utf8"));
const turnsOf = (name) => extractQoderTurns(records(name));
const messagesOf = (path, cfg = {}) => {
  const { turns, toolNameById } = extractUnseenQoderTurns(path);
  return buildIncrementalCapturePlan(turns, {}, cfg, { toolNameById }).payloads;
};
const toolParts = (message) => (message.parts || []).filter((part) => part.type === "tool");

test("one human ask and two model responses come out of a tool-use transcript", () => {
  const turns = turnsOf("transcript-tooluse");
  assert.deepEqual(turns.map((t) => [t.turnId, t.role]), [
    ["2", "user"],
    ["12", "assistant"],
    ["18", "assistant"],
  ]);
  assert.match(turns[0].content, /^Read the file a\.txt/);
});

test("the per-block records of one response fold into one assistant turn, in record order", () => {
  const [, first] = turnsOf("transcript-tooluse");
  assert.equal(first.content, "I'll read the file now.");
  assert.deepEqual(first.payload.content.map((block) => block.type), ["text", "tool_use", "tool_result"]);
});

test("a tool call and the result answering it go out as paired parts, never a user turn", () => {
  const messages = messagesOf(fixture("transcript-tooluse"));
  assert.deepEqual(messages.map((m) => m.role), ["user", "assistant", "assistant"]);
  const [call, result] = toolParts(messages[1]);
  assert.equal(messages[1].parts[0].text, "I'll read the file now.");
  assert.deepEqual(
    [call.tool_name, call.tool_status, call.tool_input.file_path],
    ["Read", "running", "/home/dev/scratch/probe-ws/a.txt"],
  );
  assert.deepEqual([result.tool_name, result.tool_id, result.tool_status], ["Read", call.tool_id, "completed"]);
  assert.match(result.tool_output, /1\thello/);
});

test("an mcp_call is unwrapped to the tool it names, and an error result says so", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-qd-turns-"));
  try {
    const path = join(dir, "s.jsonl");
    writeFileSync(path, [
      { type: "user", origin: { kind: "human" }, message: { role: "user", content: [{ type: "text", text: "search my notes" }] } },
      { type: "assistant", responseGroupId: "g", message: { role: "assistant", content: [{ type: "tool_use", id: "c1", name: "mcp_call", input: { toolName: "mcp__openviking__search", arguments: { query: "notes" } } }] } },
      { type: "user", message: { role: "user", content: [{ type: "tool_result", tool_use_id: "c1", is_error: true, content: "denied" }] } },
    ].map((record) => JSON.stringify(record)).join("\n"));
    const [call, result] = toolParts(messagesOf(path)[1]);
    assert.deepEqual([call.tool_name, call.tool_input], ["mcp__openviking__search", { query: "notes" }]);
    assert.deepEqual([result.tool_name, result.tool_status, result.tool_output], ["mcp__openviking__search", "error", "denied"]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("injected hook context and other attachments are never captured", () => {
  for (const name of ["transcript-tooluse", "transcript-compact", "transcript-subagent-skill"]) {
    const injected = records(name)
      .filter((r) => r.type === "attachment")
      .map((r) => r.attachment.output || r.attachment.content)
      .filter(Boolean);
    assert.ok(injected.some((text) => /secret word|session word|prompt word|plain word/.test(text)));
    const all = JSON.stringify(turnsOf(name));
    for (const text of injected) assert.ok(!all.includes(text), `${name}: ${text}`);
  }
});

test("thinking blocks are dropped", () => {
  const all = JSON.stringify(turnsOf("transcript-tooluse"));
  assert.ok(!all.includes("(model reasoning)"));
});

test("a compaction appends a summary that is not a human turn", () => {
  const turns = turnsOf("transcript-compact");
  assert.deepEqual(turns.map((t) => t.role), ["user", "assistant", "user", "assistant"]);
  assert.ok(!turns.some((t) => t.content.includes("This session is being continued")));
});

test("a skill body is host-authored and stays out", () => {
  const turns = turnsOf("transcript-subagent-skill");
  assert.equal(turns.filter((t) => t.role === "user").length, 1);
  assert.ok(!JSON.stringify(turns).includes("Base directory for this skill"));
  assert.deepEqual(
    turns[1].payload.content.filter((block) => block.type === "tool_use").map((block) => block.name),
    ["Skill", "Agent"],
  );
});

test("subagent records are sidechain and produce no turns", () => {
  assert.deepEqual(turnsOf("subagent-explore"), []);
});

test("a non-human origin is not a user ask", () => {
  const turns = extractQoderTurns([
    { type: "user", origin: { kind: "task-notification" }, message: { role: "user", content: [{ type: "text", text: "done" }] } },
    { type: "user", origin: { kind: "human" }, message: { role: "user", content: [{ type: "text", text: "real ask" }] } },
  ]);
  assert.deepEqual(turns.map((t) => t.content), ["real ask"]);
});

test("captureAssistantTurns off sends only the human turns", () => {
  const messages = messagesOf(fixture("transcript-tooluse"), { captureAssistantTurns: false });
  assert.deepEqual(messages.map((m) => m.role), ["user"]);
});

test("the cursor returns only the turns after the last captured one", () => {
  const path = fixture("transcript-compact");
  assert.equal(extractUnseenQoderTurns(path).turns.length, 4);
  const after = extractUnseenQoderTurns(path, "14");
  assert.deepEqual(after.turns.map((t) => t.turnId), ["21", "26"]);
  assert.deepEqual(buildQoderTurns({ transcript_path: path }, { lastTurnId: "26" }).turns, []);
});

test("a transcript that grows keeps the turn ids it already had", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-qd-turns-"));
  try {
    const lines = readFileSync(fixture("transcript-compact"), "utf8").split("\n");
    const path = join(dir, "s.jsonl");
    writeFileSync(path, `${lines.slice(0, 20).join("\n")}\n`);
    const early = extractUnseenQoderTurns(path).turns.map((t) => t.turnId);
    writeFileSync(path, lines.join("\n"));
    const later = extractUnseenQoderTurns(path).turns.map((t) => t.turnId);
    assert.deepEqual(later.slice(0, early.length), early);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("a missing or unreadable transcript is reported, not read as empty", () => {
  assert.equal(extractUnseenQoderTurns("").status, "missing");
  const r = extractUnseenQoderTurns(join(FIXTURES, "no-such-session.jsonl"));
  assert.equal(r.status, "unreadable");
  assert.equal(r.error, "ENOENT");
});
