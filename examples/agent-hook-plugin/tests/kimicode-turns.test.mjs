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
  assert.deepEqual(extractUnseenKimicodeTurns(empty), { status: "ok", turns: [] });
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
