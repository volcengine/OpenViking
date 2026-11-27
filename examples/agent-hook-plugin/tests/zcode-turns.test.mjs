import assert from "node:assert/strict";
import { copyFileSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { buildIncrementalCapturePlan } from "../hosts/incremental-turn-capture.mjs";
import { buildZcodeTurns } from "../hosts/zcode-turns.mjs";

// Recorded from a ZCode 0.18 session: four parallel tool calls (one failing,
// one MCP) in the first turn, a resumed second turn with one shell call.
const TOOLS_FIXTURE = fileURLToPath(new URL("./fixtures/zcode/rollout-tools.jsonl", import.meta.url));

const turnsOf = (input, state) => buildZcodeTurns(input, state).turns;

function withRolloutHome(sessionId, write, fn) {
  const fakeHome = mkdtempSync(join(tmpdir(), "zcode-home-"));
  const rolloutDir = join(fakeHome, ".zcode", "cli", "rollout");
  mkdirSync(rolloutDir, { recursive: true });
  write(join(rolloutDir, `model-io-${sessionId}.jsonl`));
  const originalHome = process.env.HOME;
  process.env.HOME = fakeHome;
  try {
    return fn();
  } finally {
    process.env.HOME = originalHome;
  }
}

function withRollout(entries, fn, sessionId = "test-sess-rollout") {
  return withRolloutHome(
    sessionId,
    (path) => writeFileSync(path, `${entries.map((entry) => JSON.stringify(entry)).join("\n")}\n`),
    () => fn(sessionId),
  );
}

test("buildZcodeTurns extracts user + assistant from prompt/responseText", () => {
  const turns = turnsOf({
    prompt: "what is my name?",
    responseText: "Your name is Alice.",
  });
  assert.deepEqual(turns, [
    { role: "user", content: "what is my name?" },
    { role: "assistant", content: "Your name is Alice." },
  ]);
});

test("buildZcodeTurns probes responsePreview field", () => {
  const turns = turnsOf({ prompt: "hello", responsePreview: "hi there" });
  assert.equal(turns.length, 2);
  assert.equal(turns[1].content, "hi there");
});

test("buildZcodeTurns returns only assistant turn when no prompt", () => {
  assert.deepEqual(turnsOf({ responseText: "hi there" }), [{ role: "assistant", content: "hi there" }]);
});

test("buildZcodeTurns returns empty array when both empty", () => {
  assert.equal(turnsOf({}).length, 0);
});

test("buildZcodeTurns falls back to state.pendingPrompt", () => {
  const turns = turnsOf({ responseText: "answer" }, { pendingPrompt: { prompt: "cached question" } });
  assert.equal(turns.length, 2);
  assert.equal(turns[0].role, "user");
  assert.equal(turns[0].content, "cached question");
});

test("buildZcodeTurns probes alternative field names", () => {
  const turns = turnsOf({ user_prompt: "alt prompt", assistantMessage: "alt response" });
  assert.deepEqual(turns.map((turn) => turn.content), ["alt prompt", "alt response"]);
});

test("buildZcodeTurns prefers rollout and recovers missed turns even with pendingPrompt", () => {
  const turns = withRollout([
    { turnId: "turn-001", request: { messages: [{ role: "user", content: "missed q" }] }, response: { text: "missed a" } },
    { turnId: "turn-002", request: { messages: [{ role: "user", content: "current q" }] }, response: { text: "current a" } },
  ], (sessionId) => turnsOf(
    { session_id: sessionId, responseText: "stdin current a" },
    { pendingPrompt: { prompt: "current q" } },
  ));

  assert.deepEqual(turns, [
    { role: "user", content: "missed q", turnId: "turn-001" },
    { role: "assistant", content: "missed a", turnId: "turn-001" },
    { role: "user", content: "current q", turnId: "turn-002" },
    { role: "assistant", content: "current a", turnId: "turn-002" },
  ]);
});

test("buildZcodeTurns handles missing rollout file gracefully", () => {
  const turns = turnsOf({ session_id: "nonexistent-session", responseText: "assistant only" });
  assert.deepEqual(turns, [{ role: "assistant", content: "assistant only" }]);
});

test("buildZcodeTurns respects lastTurnId in state for incremental capture", () => {
  const turns = withRollout([
    { turnId: "turn-001", request: { messages: [{ role: "user", content: "q1" }] }, response: { text: "a1" } },
    { turnId: "turn-002", request: { messages: [{ role: "user", content: "q2" }] }, response: { text: "a2" } },
    { turnId: "turn-003", request: { messages: [{ role: "user", content: "q3" }] }, response: { text: "a3" } },
  ], (sessionId) => turnsOf({ session_id: sessionId }, { lastTurnId: "turn-001" }));
  assert.deepEqual(turns.filter((turn) => turn.role === "user").map((turn) => turn.content), ["q2", "q3"]);
  assert.deepEqual(
    turns.filter((turn) => turn.turnId !== "turn-001").map((turn) => turn.content),
    ["q2", "a2", "q3", "a3"],
  );
});

test("tool calls and their results become one assistant message in host order", () => {
  const transcript = withRolloutHome(
    "sess_fixture",
    (path) => copyFileSync(TOOLS_FIXTURE, path),
    () => buildZcodeTurns({ session_id: "sess_fixture" }, {}),
  );
  const payloads = buildIncrementalCapturePlan(transcript.turns, {}, {}, {
    toolNameById: transcript.toolNameById,
  }).payloads;

  assert.deepEqual(payloads.map(({ role, turn_id }) => [role, turn_id.slice(-6)]), [
    ["user", "6afe74"],
    ["assistant", "6afe74"],
    ["user", "031cb1"],
    ["assistant", "031cb1"],
  ]);
  // The prompt, not the hook's additional context ZCode files as a user message after it.
  assert.deepEqual(payloads[0].parts, [{
    type: "text",
    text: "List the files in this folder, read hello.txt, run the shell command 'ls missing-dir', and call the echo_upper MCP tool with text 'probe'. Reply with one short line.",
  }]);
  assert.deepEqual(
    payloads[1].parts.map((part) => [part.type, part.tool_name, part.tool_status]),
    [
      ["tool", "Bash", "running"],
      ["tool", "Read", "running"],
      ["tool", "Bash", "running"],
      ["tool", "mcp__echo__echo_upper", "running"],
      ["tool", "Bash", "completed"],
      ["tool", "Read", "completed"],
      ["tool", "Bash", "error"],
      ["tool", "mcp__echo__echo_upper", "completed"],
      ["text", undefined, undefined],
    ],
  );
  assert.deepEqual(payloads[3].parts, [
    {
      type: "tool",
      tool_id: "call_00_zqm5u9mr9pcv48e45f26d07w",
      tool_name: "Bash",
      tool_status: "running",
      tool_input: { command: "echo second-turn", description: "Print second-turn" },
    },
    {
      type: "tool",
      tool_id: "call_00_zqm5u9mr9pcv48e45f26d07w",
      tool_name: "Bash",
      tool_status: "completed",
      tool_output: "second-turn",
    },
    { type: "text", text: "Output: `second-turn`" },
  ]);
});

test("delta lines, failed attempts and other query sources assemble one turn", () => {
  const call = { id: "call-1", name: "Bash", input: { command: "pwd" } };
  const result = { role: "tool", content: "/w", toolCallId: "call-1", toolName: "Bash", isError: false };
  const turns = withRollout([
    { type: "model_io", turnId: "t1", querySource: "main_turn",
      request: { messagesKind: "full", messages: [{ role: "user", content: "q1" }] },
      response: { text: "a1", toolCalls: [] } },
    { type: "model_io", turnId: "t2", querySource: "main_turn",
      request: { messagesKind: "delta", messageOffset: 1, messages: [{ role: "assistant", content: "a1" }, { role: "user", content: "q2" }] },
      response: { text: "", toolCalls: [call] } },
    { type: "model_io", turnId: "t2", querySource: "main_turn", error: { message: "overloaded" },
      request: { messagesKind: "delta", messageOffset: 3, messages: [{ role: "assistant", content: "", toolCalls: [call] }, result] } },
    { type: "model_io", turnId: "t2", querySource: "main_turn",
      request: { messagesKind: "delta", messageOffset: 3, messages: [{ role: "assistant", content: "", toolCalls: [call] }, result] },
      response: { text: "done", toolCalls: [] } },
    { type: "model_io", turnId: "t2", querySource: "compact",
      request: { messages: [{ role: "user", content: "summarize" }] },
      response: { text: "SUMMARY", toolCalls: [] } },
  ], (sessionId) => turnsOf({ session_id: sessionId }, {}));

  assert.deepEqual(turns, [
    { role: "user", content: "q1", turnId: "t1" },
    { role: "assistant", content: "a1", turnId: "t1" },
    { role: "user", content: "q2", turnId: "t2" },
    {
      role: "assistant",
      content: "done",
      turnId: "t2",
      payload: {
        role: "assistant",
        content: [
          { type: "tool_use", ...call },
          { type: "tool_result", tool_use_id: "call-1", name: "Bash", content: "/w", is_error: false },
          { type: "text", text: "done" },
        ],
      },
    },
  ]);
});

// ---------------------------------------------------------------------------
// Concurrent session isolation: two sessions in the same workspace must not
// cross-read each other's rollout data
// ---------------------------------------------------------------------------

test("isolation: two sessions read their own rollout files, not each other's", () => {
  const fakeHome = mkdtempSync(join(tmpdir(), "zcode-iso-"));
  const rolloutDir = join(fakeHome, ".zcode", "cli", "rollout");
  mkdirSync(rolloutDir, { recursive: true });

  const sessA = "sess-isolation-AAA";
  const sessB = "sess-isolation-BBB";
  writeFileSync(
    join(rolloutDir, `model-io-${sessA}.jsonl`),
    JSON.stringify({ turnId: "turn-A1", request: { messages: [{ role: "user", content: "amber-sentinel-A" }] }, response: { text: "response-A" } }) + "\n",
  );
  writeFileSync(
    join(rolloutDir, `model-io-${sessB}.jsonl`),
    JSON.stringify({ turnId: "turn-B1", request: { messages: [{ role: "user", content: "sapphire-sentinel-B" }] }, response: { text: "response-B" } }) + "\n",
  );

  const originalHome = process.env.HOME;
  process.env.HOME = fakeHome;
  const turnsA = turnsOf({ session_id: sessA, responseText: "ignored" });
  const turnsB = turnsOf({ session_id: sessB, responseText: "ignored" });
  process.env.HOME = originalHome;

  const aContents = turnsA.map((t) => t.content).join(" ");
  assert.ok(aContents.includes("amber-sentinel-A"), "session A should see its own content");
  assert.ok(!aContents.includes("sapphire-sentinel-B"), "session A must NOT see session B's content");
  const bContents = turnsB.map((t) => t.content).join(" ");
  assert.ok(bContents.includes("sapphire-sentinel-B"), "session B should see its own content");
  assert.ok(!bContents.includes("amber-sentinel-A"), "session B must NOT see session A's content");
  assert.equal(turnsA[0].turnId, "turn-A1");
  assert.equal(turnsB[0].turnId, "turn-B1");
});

test("isolation: independent lastTurnId state per session", () => {
  const fakeHome = mkdtempSync(join(tmpdir(), "zcode-iso-state-"));
  const rolloutDir = join(fakeHome, ".zcode", "cli", "rollout");
  mkdirSync(rolloutDir, { recursive: true });

  const sessA = "sess-state-AAA";
  const sessB = "sess-state-BBB";
  writeFileSync(
    join(rolloutDir, `model-io-${sessA}.jsonl`),
    [
      JSON.stringify({ turnId: "A-001", request: { messages: [{ role: "user", content: "A1" }] }, response: { text: "a1" } }),
      JSON.stringify({ turnId: "A-002", request: { messages: [{ role: "user", content: "A2" }] }, response: { text: "a2" } }),
    ].join("\n") + "\n",
  );
  writeFileSync(
    join(rolloutDir, `model-io-${sessB}.jsonl`),
    [
      JSON.stringify({ turnId: "B-001", request: { messages: [{ role: "user", content: "B1" }] }, response: { text: "b1" } }),
      JSON.stringify({ turnId: "B-002", request: { messages: [{ role: "user", content: "B2" }] }, response: { text: "b2" } }),
    ].join("\n") + "\n",
  );

  const originalHome = process.env.HOME;
  process.env.HOME = fakeHome;
  const turnsA = turnsOf({ session_id: sessA, responseText: "ignored" }, { lastTurnId: "A-001" });
  const turnsB = turnsOf({ session_id: sessB, responseText: "ignored" }, {});
  process.env.HOME = originalHome;

  assert.deepEqual(turnsA.filter((turn) => turn.turnId !== "A-001").map((turn) => turn.content), ["A2", "a2"]);
  assert.equal(turnsB.length, 4);
  assert.equal(turnsB[0].turnId, "B-001");
  assert.equal(turnsB[2].turnId, "B-002");
});
