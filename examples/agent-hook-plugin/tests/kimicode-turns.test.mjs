import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  buildKimicodeTurns,
  cleanKimicodeText,
  extractUnseenKimicodeTurns,
} from "../hosts/kimicode-turns.mjs";
import { buildIncrementalCapturePlan } from "../hosts/incremental-turn-capture.mjs";

test("content parts are normalized and injected context is removed", () => {
  assert.equal(cleanKimicodeText([
    { type: "text", text: "hello" },
    { type: "text", text: "<openviking-context>old</openviking-context>world" },
  ]), "hello\n world");
});

test("wire decoding groups prompt and assistant text under a stable turn id", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-kimi-wire-"));
  const wire = join(dir, "wire.jsonl");
  writeFileSync(wire, [
    JSON.stringify({ type: "turn.prompt", input: [{ type: "text", text: "question" }] }),
    JSON.stringify({ type: "context.append_message", message: { role: "user", content: [{ type: "text", text: "question" }] } }),
    JSON.stringify({ type: "context.append_loop_event", event: { type: "content.part", turnId: "7", part: { type: "think", think: "hidden" } } }),
    JSON.stringify({ type: "context.append_loop_event", event: { type: "content.part", turnId: "7", part: { type: "text", text: "answer" } } }),
  ].join("\n") + "\n");

  assert.deepEqual(extractUnseenKimicodeTurns(wire).turns, [
    { role: "user", content: "question", turnId: "7" },
    { role: "assistant", content: "answer", turnId: "7" },
  ]);
});

test("host-generated user messages do not replace the prompt", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-kimi-wire-"));
  const wire = join(dir, "wire.jsonl");
  writeFileSync(wire, [
    JSON.stringify({
      type: "context.append_message",
      message: {
        role: "user",
        origin: { kind: "hook_result", event: "UserPromptSubmit" },
        content: [{
          type: "text",
          text: '<hook_result hook_event="UserPromptSubmit">\n<openviking-context source="recall">old</openviking-context>\n</hook_result>',
        }],
      },
    }),
    JSON.stringify({ type: "turn.prompt", turnId: 0, origin: { kind: "user" }, input: [{ type: "text", text: "Fix release.json." }] }),
    JSON.stringify({ type: "context.append_message", message: { role: "user", origin: { kind: "user" }, content: [{ type: "text", text: "Fix release.json." }] } }),
    JSON.stringify({
      type: "context.append_message",
      message: {
        role: "user",
        origin: { kind: "injection", variant: "reminder" },
        content: [{ type: "text", text: "<system-reminder>\nhost reminder\n</system-reminder>" }],
      },
    }),
    JSON.stringify({ type: "context.append_loop_event", event: { type: "content.part", turnId: 0, part: { type: "text", text: "Done." } } }),
    JSON.stringify({ type: "turn.ended", turnId: 0, reason: "completed" }),
  ].join("\n") + "\n");

  assert.deepEqual(extractUnseenKimicodeTurns(wire).turns, [
    { role: "user", content: "Fix release.json.", turnId: "0" },
    { role: "assistant", content: "Done.", turnId: "0" },
  ]);
});

test("assistant text from consecutive steps is separated", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-kimi-wire-"));
  const wire = join(dir, "wire.jsonl");
  writeFileSync(wire, [
    JSON.stringify({ type: "turn.prompt", input: [{ type: "text", text: "question" }] }),
    JSON.stringify({ type: "context.append_loop_event", event: { type: "content.part", turnId: "3", part: { type: "text", text: "Reading files." } } }),
    JSON.stringify({ type: "context.append_loop_event", event: { type: "content.part", turnId: "3", part: { type: "text", text: "Done." } } }),
  ].join("\n") + "\n");

  assert.deepEqual(extractUnseenKimicodeTurns(wire).turns, [
    { role: "user", content: "question", turnId: "3" },
    { role: "assistant", content: "Reading files.\nDone.", turnId: "3" },
  ]);
});

test("tool calls and their results fold into the turn's assistant message", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-kimi-wire-"));
  const wire = join(dir, "wire.jsonl");
  const loop = (event) => JSON.stringify({ type: "context.append_loop_event", event });
  writeFileSync(wire, [
    JSON.stringify({ type: "turn.prompt", input: [{ type: "text", text: "list the files" }] }),
    loop({ type: "step.begin", uuid: "s1", turnId: "4" }),
    loop({ type: "content.part", stepUuid: "s1", turnId: "4", part: { type: "text", text: "Checking the folder first." } }),
    loop({ type: "tool.call", stepUuid: "s1", toolCallId: "c1", name: "Shell", args: { command: "ls" } }),
    loop({ type: "tool.call", stepUuid: "s1", turnId: "4", toolCallId: "c2", name: "ReadFile", args: { path: "a.txt" } }),
    loop({ type: "tool.call", stepUuid: "s1", turnId: "4", toolCallId: "c3" }),
    loop({ type: "tool.result", toolCallId: "c1", result: { output: [{ type: "text", text: "a.txt" }, { type: "text", text: "b.txt" }] } }),
    loop({ type: "tool.result", toolCallId: "c2", result: { output: "no such file", isError: true } }),
    loop({ type: "tool.result", toolCallId: "ghost", result: { output: "orphan" } }),
    loop({ type: "tool.result", toolCallId: "c1" }),
    loop({ type: "content.part", stepUuid: "s2", turnId: "4", part: { type: "text", text: "There are two files." } }),
    JSON.stringify({ type: "turn.ended", turnId: 4, reason: "completed" }),
  ].join("\n") + "\n");

  const transcript = extractUnseenKimicodeTurns(wire);
  assert.deepEqual(transcript.turns.map(({ role, content, turnId }) => ({ role, content, turnId })), [
    { role: "user", content: "list the files", turnId: "4" },
    { role: "assistant", content: "Checking the folder first.\nThere are two files.", turnId: "4" },
  ]);
  const plan = buildIncrementalCapturePlan(transcript.turns, {}, {}, { toolNameById: transcript.toolNameById });
  assert.deepEqual(plan.payloads, [
    { role: "user", content: "list the files", turn_id: "4" },
    {
      role: "assistant",
      parts: [
        { type: "text", text: "Checking the folder first.\n\nThere are two files." },
        { type: "tool", tool_id: "c1", tool_name: "Shell", tool_status: "running", tool_input: { command: "ls" } },
        { type: "tool", tool_id: "c2", tool_name: "ReadFile", tool_status: "running", tool_input: { path: "a.txt" } },
        { type: "tool", tool_id: "c1", tool_name: "Shell", tool_status: "completed", tool_output: "a.txt\nb.txt" },
        { type: "tool", tool_id: "c2", tool_name: "ReadFile", tool_status: "error", tool_output: "no such file" },
      ],
      turn_id: "4",
    },
  ]);
});

test("turn.ended closes an interrupted user-only turn", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-kimi-wire-"));
  const wire = join(dir, "wire.jsonl");
  writeFileSync(wire, [
    JSON.stringify({ type: "turn.prompt", input: [{ type: "text", text: "cancel me" }] }),
    JSON.stringify({ type: "turn.ended", turnId: "8", reason: "cancelled" }),
  ].join("\n") + "\n");
  assert.deepEqual(extractUnseenKimicodeTurns(wire).turns, [
    { role: "user", content: "cancel me", turnId: "8" },
  ]);
});

test("a missing wire waits for a stable transcript instead of guessing hook fields", () => {
  const previous = process.env.KIMI_CODE_HOME;
  process.env.KIMI_CODE_HOME = mkdtempSync(join(tmpdir(), "ov-kimi-home-"));
  try {
    assert.deepEqual(buildKimicodeTurns(
      { session_id: "missing" },
      { pendingPrompt: { prompt: "question" } },
    ), { status: "missing", turns: [] });
  } finally {
    if (previous === undefined) delete process.env.KIMI_CODE_HOME;
    else process.env.KIMI_CODE_HOME = previous;
  }
});

test("an unreadable wire is distinct from a readable wire with no new turns", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-kimi-wire-"));
  const unreadable = join(dir, "wire.jsonl");
  mkdirSync(unreadable);
  assert.equal(extractUnseenKimicodeTurns(unreadable).status, "unreadable");

  const empty = join(dir, "empty.jsonl");
  writeFileSync(empty, "");
  assert.deepEqual(extractUnseenKimicodeTurns(empty), { status: "ok", turns: [], toolNameById: {} });
});

test("a rotated wire restarts from its current first turn when the old cursor is absent", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-kimi-wire-"));
  const wire = join(dir, "wire.jsonl");
  writeFileSync(wire, [
    JSON.stringify({ type: "turn.prompt", input: [{ type: "text", text: "new question" }] }),
    JSON.stringify({
      type: "context.append_loop_event",
      event: { type: "content.part", turnId: "12", part: { type: "text", text: "new answer" } },
    }),
  ].join("\n") + "\n");

  assert.deepEqual(extractUnseenKimicodeTurns(wire, "rotated-away").turns, [
    { role: "user", content: "new question", turnId: "12" },
    { role: "assistant", content: "new answer", turnId: "12" },
  ]);
});
