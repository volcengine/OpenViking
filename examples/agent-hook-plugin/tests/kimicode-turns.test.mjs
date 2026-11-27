import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  buildKimicodeTurns,
  cleanKimicodeText,
  extractUnseenKimicodeTurns,
} from "../hosts/kimicode-turns.mjs";
import { buildIncrementalCapturePlan, captureIncrementalTurns } from "../hosts/incremental-turn-capture.mjs";

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), "fixtures", "kimicode");

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

// Kimi 2.8 records a UserPromptSubmit hook's output and an activated skill's
// body as tagged parts of the user's own message.
test("host-generated parts and messages are not taken for the user's words", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-kimi-wire-"));
  const wire = join(dir, "wire.jsonl");
  const prompt = (turnId, content, origin = { kind: "user" }) => [
    JSON.stringify({ type: "turn.prompt", turnId, origin, input: content }),
    JSON.stringify({ type: "context.append_message", message: { role: "user", origin, content } }),
  ];
  const reply = (turnId, text) => [
    JSON.stringify({ type: "context.append_loop_event", event: { type: "content.part", turnId: String(turnId), part: { type: "text", text } } }),
    JSON.stringify({ type: "turn.ended", turnId, reason: "completed" }),
  ];
  writeFileSync(wire, [
    ...prompt(0, [
      {
        type: "text",
        text: 'User activated the skill "openviking-memory:openviking-memory".\n\n<skill-loaded name="openviking-memory:openviking-memory" trigger="user-slash">skill body</skill-loaded>',
        meta: { source: "skill activation", activationId: "a1" },
      },
      { type: "text", text: "/skill:openviking-memory:openviking-memory reply with noted" },
    ], { kind: "user", skillActivations: [{ activationId: "a1", skillName: "openviking-memory:openviking-memory" }] }),
    JSON.stringify({
      type: "context.append_message",
      message: {
        role: "user",
        origin: { kind: "injection", variant: "permission_mode" },
        content: [{ type: "text", text: "<system-reminder>\nhost reminder\n</system-reminder>" }],
      },
    }),
    ...reply(0, "noted"),
    ...prompt(1, [
      {
        type: "text",
        text: '<hook_result hook_event="UserPromptSubmit">\n<openviking-context source="session-start">profile</openviking-context>\n</hook_result>',
        meta: { contentType: "text/xml", source: "user prompt submit hook" },
      },
      { type: "text", text: "Fix release.json." },
    ]),
    ...reply(1, "Done."),
  ].join("\n") + "\n");

  assert.deepEqual(extractUnseenKimicodeTurns(wire).turns, [
    { role: "user", content: "/skill:openviking-memory:openviking-memory reply with noted", turnId: "0" },
    { role: "assistant", content: "noted", turnId: "0" },
    { role: "user", content: "Fix release.json.", turnId: "1" },
    { role: "assistant", content: "Done.", turnId: "1" },
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

// Recorded from a Kimi Code 2.8.1 session with a shell command, a file read, a
// failing command and an MCP call; paths and the account name are replaced.
test("a recorded session's tool calls and results are sent in host order", () => {
  const transcript = extractUnseenKimicodeTurns(join(FIXTURES, "wire.jsonl"));
  const plan = buildIncrementalCapturePlan(transcript.turns, {}, {}, { toolNameById: transcript.toolNameById });
  const outline = plan.payloads.map(({ role, turn_id, parts }) => ({
    role,
    turn_id,
    parts: parts.map((part) => (part.type === "text" ? `text: ${part.text.slice(0, 24)}` : `${part.tool_name} ${part.tool_status}`)),
  }));
  const ovRead = "mcp__openviking__read";
  assert.deepEqual(outline, [
    { role: "user", turn_id: "0", parts: ["text: List the files in this f"] },
    {
      role: "assistant",
      turn_id: "0",
      parts: [
        "text: I'll run the four steps ",
        "Bash running",
        "Read running",
        "Bash completed",
        "Read completed",
        "Bash running",
        `${ovRead} running`,
        "Bash error",
        `${ovRead} completed`,
        "text: Done: the folder holds o",
      ],
    },
    { role: "user", turn_id: "1", parts: ["text: Run the shell command 'e"] },
    { role: "assistant", turn_id: "1", parts: ["Bash running", "Bash completed", "text: ok"] },
  ]);
  const parts = plan.payloads[1].parts;
  assert.deepEqual(parts[1], {
    type: "tool",
    tool_id: "call_00_mk47sz7usjjzd7upuk4h5qze",
    tool_name: "Bash",
    tool_status: "running",
    tool_input: { command: "ls -la" },
  });
  assert.equal(parts[7].tool_id, "call_00_tdjuny0gqfpmm136zw3nidd3");
  assert.equal(parts[7].tool_output, "cat: missing-file.txt: No such file or directory\nCommand failed with exit code: 1.");
  assert.equal(parts[8].tool_output, "mock content of viking://resources/demo");
});

test("a compaction in the middle of a turn leaves the rest of the turn to the next capture", async () => {
  const lines = readFileSync(join(FIXTURES, "wire.jsonl"), "utf8").split("\n").filter(Boolean);
  const wire = join(mkdtempSync(join(tmpdir(), "ov-kimi-wire-")), "wire.jsonl");
  const sent = [];
  const ctx = {
    cfg: { enabled: true, autoCapture: true },
    sessionId: "kc-midturn",
    peerId: "",
    log: () => {},
    fetchJSON: async (path, request = {}) => {
      if (path.endsWith("/messages/batch")) sent.push(...JSON.parse(request.body).messages);
      return { ok: true, status: 200, result: { pending_tokens: 0 } };
    },
  };
  const capture = (state, event) =>
    captureIncrementalTurns(ctx, state, event, extractUnseenKimicodeTurns(wire, state.lastTurnId), { cleanText: cleanKimicodeText });
  writeFileSync(wire, `${lines.slice(0, 12).join("\n")}\n`);
  const state = await capture({}, "pre-compact");
  assert.deepEqual(sent.map(({ role, turn_id }) => `${role}#${turn_id}`), ["user#0"]);
  writeFileSync(wire, `${lines.join("\n")}\n`);
  await capture(state, "stop");
  const turn0 = sent.find(({ role, turn_id }) => role === "assistant" && turn_id === "0");
  assert.equal(turn0.parts.at(-1).text.slice(0, 15), "Done: the folde");
  assert.deepEqual(sent.map(({ role, turn_id }) => `${role}#${turn_id}`), ["user#0", "assistant#0", "user#1", "assistant#1"]);
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
