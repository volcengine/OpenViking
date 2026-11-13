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

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), "fixtures", "qoder");
const fixture = (name) => join(FIXTURES, `${name}.jsonl`);
const records = (name) => parseTranscript(readFileSync(fixture(name), "utf8"));
const turnsOf = (name, cfg) => extractQoderTurns(records(name), cfg);

test("one human ask and two model responses come out of a tool-use transcript", () => {
  const turns = turnsOf("transcript-tooluse");
  assert.deepEqual(turns.map((t) => [t.turnId, t.role]), [
    ["2", "user"],
    ["12", "assistant"],
    ["18", "assistant"],
  ]);
  assert.match(turns[0].content, /^Read the file a\.txt/);
});

test("the per-block records of one response fold into one assistant turn with its tool call", () => {
  const [, first] = turnsOf("transcript-tooluse");
  assert.match(first.content, /^I'll read the file now\./);
  assert.match(first.content, /\[tool: Read\]/);
  assert.match(first.content, /"file_path": "\/home\/dev\/scratch\/probe-ws\/a\.txt"/);
});

test("tool results stay out of the text and never open a user turn", () => {
  const turns = turnsOf("transcript-tooluse");
  assert.equal(turns.filter((t) => t.role === "user").length, 1);
  assert.ok(!turns.some((t) => t.content.includes("1\thello")), "result bytes are not captured");
});

test("injected hook context and other attachments are never captured", () => {
  for (const name of ["transcript-tooluse", "transcript-compact", "transcript-subagent-skill"]) {
    const injected = records(name)
      .filter((r) => r.type === "attachment")
      .map((r) => r.attachment.output || r.attachment.content)
      .filter(Boolean);
    assert.ok(injected.some((text) => /secret word|session word|prompt word|plain word/.test(text)));
    const all = turnsOf(name).map((t) => t.content).join("\n");
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
  assert.match(turns[1].content, /\[tool: Skill\][\s\S]*\[tool: Agent\]/);
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

test("captureAssistantTurns off keeps only the human turns", () => {
  const turns = turnsOf("transcript-tooluse", { captureAssistantTurns: false });
  assert.deepEqual(turns.map((t) => t.role), ["user"]);
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
