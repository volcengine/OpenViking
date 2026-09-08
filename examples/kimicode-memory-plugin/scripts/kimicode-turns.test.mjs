import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  buildKimicodeTurns,
  cleanKimicodeText,
  extractUnseenWireTurns,
} from "./kimicode-turns.mjs";

test("cleanKimicodeText strips openviking-context blocks", () => {
  const input = 'hello <openviking-context source="test">secret</openviking-context> world';
  assert.equal(cleanKimicodeText(input), "hello  world");
});

test("cleanKimicodeText strips relevant-memory blocks", () => {
  const input = "text <relevant-memory>old</relevant-memory> here";
  assert.equal(cleanKimicodeText(input), "text  here");
});

test("extractUnseenWireTurns reads user prompt and assistant text parts", () => {
  const dir = mkdtempSync(join(tmpdir(), "kc-wire-"));
  const wire = join(dir, "wire.jsonl");
  writeFileSync(
    wire,
    [
      JSON.stringify({ type: "metadata", protocol_version: "1.4" }),
      JSON.stringify({
        type: "turn.prompt",
        input: [{ type: "text", text: "hi" }],
        origin: { kind: "user" },
      }),
      JSON.stringify({
        type: "context.append_loop_event",
        event: {
          type: "content.part",
          turnId: "0",
          part: { type: "think", think: "ignore me" },
        },
      }),
      JSON.stringify({
        type: "context.append_loop_event",
        event: {
          type: "content.part",
          turnId: "0",
          part: { type: "text", text: "Hi! How can I help?" },
        },
      }),
    ].join("\n") + "\n",
  );
  const { available, turns } = extractUnseenWireTurns(wire, null);
  assert.equal(available, true);
  assert.deepEqual(turns, [
    { role: "user", content: "hi", turnId: "0" },
    { role: "assistant", content: "Hi! How can I help?", turnId: "0" },
  ]);
});

test("extractUnseenWireTurns skips turns up to lastTurnId", () => {
  const dir = mkdtempSync(join(tmpdir(), "kc-wire-"));
  const wire = join(dir, "wire.jsonl");
  writeFileSync(
    wire,
    [
      JSON.stringify({
        type: "turn.prompt",
        input: [{ type: "text", text: "first" }],
      }),
      JSON.stringify({
        type: "context.append_loop_event",
        event: { type: "content.part", turnId: "0", part: { type: "text", text: "a1" } },
      }),
      JSON.stringify({
        type: "turn.prompt",
        input: [{ type: "text", text: "second" }],
      }),
      JSON.stringify({
        type: "context.append_loop_event",
        event: { type: "content.part", turnId: "1", part: { type: "text", text: "a2" } },
      }),
    ].join("\n") + "\n",
  );
  const { turns } = extractUnseenWireTurns(wire, "0");
  assert.deepEqual(turns, [
    { role: "user", content: "second", turnId: "1" },
    { role: "assistant", content: "a2", turnId: "1" },
  ]);
});

test("buildKimicodeTurns uses session_index.jsonl to find the wire log", () => {
  const home = mkdtempSync(join(tmpdir(), "kc-home-"));
  const sessionId = "session_abc";
  const sessionDir = join(home, "sessions", "wd_x", sessionId);
  mkdirSync(join(sessionDir, "agents", "main"), { recursive: true });
  writeFileSync(
    join(home, "session_index.jsonl"),
    JSON.stringify({ sessionId, sessionDir, workDir: "/tmp" }) + "\n",
  );
  writeFileSync(
    join(sessionDir, "agents", "main", "wire.jsonl"),
    JSON.stringify({
      type: "turn.prompt",
      input: [{ type: "text", text: "indexed" }],
    }) +
      "\n" +
      JSON.stringify({
        type: "context.append_loop_event",
        event: { type: "content.part", turnId: "7", part: { type: "text", text: "ok" } },
      }) +
      "\n",
  );
  const original = process.env.KIMI_CODE_HOME;
  process.env.KIMI_CODE_HOME = home;
  try {
    const turns = buildKimicodeTurns({ session_id: sessionId }, {});
    assert.equal(turns[0].content, "indexed");
    assert.equal(turns[1].turnId, "7");
  } finally {
    if (original === undefined) delete process.env.KIMI_CODE_HOME;
    else process.env.KIMI_CODE_HOME = original;
  }
});

test("turn.prompt plus append_message is a single user turn", () => {
  const dir = mkdtempSync(join(tmpdir(), "kc-wire-"));
  const wire = join(dir, "wire.jsonl");
  writeFileSync(
    wire,
    [
      JSON.stringify({
        type: "turn.prompt",
        input: [{ type: "text", text: "hi" }],
      }),
      JSON.stringify({
        type: "context.append_message",
        message: { role: "user", content: [{ type: "text", text: "hi" }] },
      }),
      JSON.stringify({
        type: "context.append_loop_event",
        event: {
          type: "content.part",
          turnId: "0",
          part: { type: "text", text: "hello" },
        },
      }),
    ].join("\n") + "\n",
  );
  const { turns } = extractUnseenWireTurns(wire, null);
  assert.equal(turns.filter((turn) => turn.role === "user").length, 1);
  assert.deepEqual(turns, [
    { role: "user", content: "hi", turnId: "0" },
    { role: "assistant", content: "hello", turnId: "0" },
  ]);
});

test("extractUnseenWireTurns closes a cancelled turn on turn.ended", () => {
  const dir = mkdtempSync(join(tmpdir(), "kc-wire-"));
  const wire = join(dir, "wire.jsonl");
  writeFileSync(
    wire,
    [
      JSON.stringify({
        type: "turn.prompt",
        input: [{ type: "text", text: "first question" }],
        origin: { kind: "user" },
      }),
      JSON.stringify({ type: "turn.ended", turnId: "0", reason: "cancelled" }),
      JSON.stringify({
        type: "turn.prompt",
        input: [{ type: "text", text: "second question" }],
        origin: { kind: "user" },
      }),
      JSON.stringify({
        type: "context.append_loop_event",
        event: {
          type: "content.part",
          turnId: "1",
          part: { type: "text", text: "answer" },
        },
      }),
    ].join("\n") + "\n",
  );
  const { turns } = extractUnseenWireTurns(wire, null);
  assert.deepEqual(turns, [
    { role: "user", content: "first question", turnId: "0" },
    { role: "user", content: "second question", turnId: "1" },
    { role: "assistant", content: "answer", turnId: "1" },
  ]);
});

test("buildKimicodeTurns falls back to stdin when wire is missing", () => {
  const original = process.env.KIMI_CODE_HOME;
  process.env.KIMI_CODE_HOME = mkdtempSync(join(tmpdir(), "kc-empty-"));
  try {
    const turns = buildKimicodeTurns(
      { session_id: "session_missing", prompt: "hello", responseText: "world" },
      {},
    );
    assert.deepEqual(turns, [
      { role: "user", content: "hello" },
      { role: "assistant", content: "world" },
    ]);
  } finally {
    if (original === undefined) delete process.env.KIMI_CODE_HOME;
    else process.env.KIMI_CODE_HOME = original;
  }
});
