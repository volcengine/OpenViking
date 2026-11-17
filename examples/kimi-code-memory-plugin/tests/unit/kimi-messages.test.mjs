/**
 * The Kimi → OpenViking message mapping.
 *
 * These tests pin the wire contract the server sees: roles, message kinds,
 * turn ids, timestamps, tool linkage, and the user-visibility rule.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  classifyUserInput,
  logicalTurnIdFor,
  mapTurnToMessages,
  slashCommandText,
} from "../../scripts/kimi-messages.mjs";
import { projectWire } from "../../scripts/wire-projector.mjs";
import {
  contentPart,
  metadata,
  stepBegin,
  stepEnd,
  toolCall,
  toolResult,
  turnEnded,
  turnPrompt,
  turnSteer,
} from "../fixtures/wire-fixtures.mjs";

const CFG = { captureMaxLength: 24000, captureToolMaxChars: 1000000, captureFilters: [] };

function turnFrom(records) {
  const entries = records.map((record, index) => ({ line: index + 1, record }));
  const projection = projectWire(entries);
  assert.equal(projection.status, "ok");
  return projection.turns[0];
}

test("a user turn maps to one user_query plus assistant steps", () => {
  const turn = turnFrom([
    metadata(),
    turnPrompt({ turnId: 0, text: "how does the reader work?" }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "It reads complete lines." } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  const { payloads } = mapTurnToMessages(turn, CFG);
  assert.equal(payloads.length, 2);
  assert.equal(payloads[0].role, "user");
  assert.equal(payloads[0].message_kind, "user_query");
  assert.deepEqual(payloads[0].parts, [{ type: "text", text: "how does the reader work?" }]);
  assert.equal(payloads[1].role, "assistant");
  assert.equal(payloads[1].message_kind, "assistant_step");
  // No id and no peer: the shared sender stamps the peer, the server assigns ids.
  assert.equal(payloads[0].id, undefined);
  assert.equal(payloads[0].peer_id, undefined);
});

test("timestamps are ISO strings derived from epoch milliseconds", () => {
  const turn = turnFrom([
    metadata(),
    turnPrompt({ turnId: 0, text: "how do I build this?", time: 1760000000000 }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "Run the tests first." } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  const { payloads } = mapTurnToMessages(turn, CFG);
  assert.equal(payloads[0].created_at, new Date(1760000000000).toISOString());
  assert.match(payloads[0].created_at, /^\d{4}-\d{2}-\d{2}T/);
});

test("tool calls stay on the assistant message and the result becomes a tool_transport user message", () => {
  const turn = turnFrom([
    metadata(),
    turnPrompt({ turnId: 0, text: "read the file" }),
    stepBegin({ turnId: 0, step: 1 }),
    toolCall({ turnId: 0, step: 1, toolCallId: "call-1", name: "Read", args: { file_path: "/tmp/a.txt" } }),
    stepEnd({ turnId: 0, step: 1 }),
    stepBegin({ turnId: 0, step: 2 }),
    toolResult({ turnId: 0, toolCallId: "call-1", output: "hello", durationMs: 42 }),
    stepEnd({ turnId: 0, step: 2 }),
    turnEnded({ turnId: 0 }),
  ]);
  const { payloads } = mapTurnToMessages(turn, CFG);
  const assistant = payloads.find((payload) => payload.role === "assistant");
  assert.ok(assistant);
  assert.deepEqual(assistant.parts[0], {
    type: "tool",
    tool_id: "call-1",
    tool_status: "running",
    tool_name: "Read",
    tool_input: { file_path: "/tmp/a.txt" },
  });

  const transport = payloads.find((payload) => payload.message_kind === "tool_transport");
  assert.ok(transport);
  assert.equal(transport.role, "user");
  assert.equal(transport.parts[0].tool_id, "call-1");
  assert.equal(transport.parts[0].tool_output, "hello");
  assert.equal(transport.parts[0].tool_status, "completed");
  assert.equal(transport.parts[0].duration_ms, 42);
});

test("a failed tool result is marked error, and a missing duration is not invented", () => {
  const turn = turnFrom([
    metadata(),
    turnPrompt({ turnId: 0, text: "run it" }),
    stepBegin({ turnId: 0, step: 1 }),
    toolCall({ turnId: 0, step: 1, toolCallId: "call-2", name: "Bash", args: { command: "false" } }),
    toolResult({ turnId: 0, toolCallId: "call-2", output: "exit 1", isError: true }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  const { payloads } = mapTurnToMessages(turn, CFG);
  const transport = payloads.find((payload) => payload.message_kind === "tool_transport");
  assert.equal(transport.parts[0].tool_status, "error");
  assert.equal("duration_ms" in transport.parts[0], false);
});

test("a steer produces two logical turns with their own ids", () => {
  const entries = [
    metadata(),
    turnPrompt({ turnId: 0, text: "first ask" }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "answer one" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnSteer({ turnId: 0, messageId: "steer-9", text: "second ask" }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "answer two" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ].map((record, index) => ({ line: index + 1, record }));
  const projection = projectWire(entries);
  assert.equal(projection.turns.length, 2);

  const mapped = projection.turns.flatMap((turn) => mapTurnToMessages(turn, CFG).payloads);
  const users = mapped.filter((payload) => payload.message_kind === "user_query");
  assert.deepEqual(users.map((payload) => payload.parts[0].text), ["first ask", "second ask"]);
  // The host assigns each logical turn its own id; the projection carries it and
  // the mapper does not append anything to it.
  const expected = projection.turns.map((turn) => String(turn.turnId));
  assert.deepEqual(users.map((payload) => payload.turn_id), expected);
  assert.equal(new Set(expected).size, 2);
});

test("logicalTurnIdFor uses the host's id and nothing else", () => {
  assert.equal(logicalTurnIdFor(7, 0), "7");
  assert.equal(logicalTurnIdFor(7, 2), "7");
  assert.equal(logicalTurnIdFor(undefined, 0, 12), "l12");
  assert.equal(logicalTurnIdFor(undefined, 1, 12), "l12-1");
});

test("the user-visibility rule matches the host's own filter", () => {
  assert.deepEqual(classifyUserInput({ origin: { kind: "user" } }), { visible: true, kind: "user" });
  assert.deepEqual(classifyUserInput({ origin: undefined }), { visible: true, kind: "user" });
  assert.equal(classifyUserInput({ origin: { kind: "user" }, trigger: "continue" }).visible, false);

  assert.equal(classifyUserInput({ origin: { kind: "skill_activation", trigger: "user-slash" } }).visible, true);
  assert.equal(classifyUserInput({ origin: { kind: "plugin_command", trigger: "user-slash" } }).visible, true);
  assert.equal(classifyUserInput({ origin: { kind: "skill_activation" } }).visible, false);

  assert.equal(classifyUserInput({ origin: { kind: "shell_command", phase: "input" } }).visible, true);
  assert.equal(classifyUserInput({ origin: { kind: "shell_command", phase: "output" } }).visible, false);

  for (const kind of ["task", "system_trigger", "injection", "hook_result"]) {
    assert.equal(classifyUserInput({ origin: { kind } }).visible, false, kind);
  }
});

test("a slash command is captured as the command, not as the skill body", () => {
  // The wire carries the command text; it is used verbatim.
  assert.equal(
    slashCommandText({ origin: { kind: "skill_activation", skillName: "openviking-memory" }, input: "/skill openviking-memory" }),
    "/skill openviking-memory",
  );
  // Without it, the host's own fields are what the user saw: it renders
  // `/<skillName> <args>` for a skill and `/<pluginId>:<commandName> <args>`
  // for a plugin command.
  assert.equal(
    slashCommandText({ origin: { kind: "skill_activation", skillName: "openviking-memory" }, input: "" }),
    "/openviking-memory",
  );
  assert.equal(
    slashCommandText({ origin: { kind: "skill_activation", skillName: "openviking-memory", skillArgs: "foo bar" }, input: "" }),
    "/openviking-memory foo bar",
  );
  assert.equal(
    slashCommandText({ origin: { kind: "plugin_command", pluginId: "ops", commandName: "deploy" }, input: "" }),
    "/ops:deploy",
  );
  assert.equal(
    slashCommandText({ origin: { kind: "plugin_command", pluginId: "ops", commandName: "deploy", commandArgs: "--dry-run" }, input: "" }),
    "/ops:deploy --dry-run",
  );
  // No name at all means nothing to say; the caller then drops the turn.
  assert.equal(slashCommandText({ origin: { kind: "skill_activation" }, input: "" }), "");
});

test("a skill activation is reconstructed from the host's own origin fields", () => {
  // The host writes `skillName`/`skillArgs`; the adapter used to read `name`,
  // which does not exist, so a real activation produced no text at all and the
  // turn was dropped as "empty" rather than shaped by the shared rules.
  const turn = turnFrom([
    metadata(),
    turnPrompt({
      turnId: 0,
      text: "User activated the skill \"openviking-memory\". Follow the loaded skill instructions.",
      origin: { kind: "skill_activation", skillName: "openviking-memory", skillArgs: "find my notes", trigger: "user-slash" },
    }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "Looking through your notes now." } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  const segment = turn.segments[0];
  assert.equal(slashCommandText(segment), "/openviking-memory find my notes");
  // The shared shaper drops a `/`-prefixed user text (`slash_command`), which is
  // its rule and not the adapter's to override: the assistant work still lands.
  const { payloads, dropped } = mapTurnToMessages(turn, CFG);
  assert.equal(payloads.some((payload) => payload.message_kind === "user_query"), false);
  assert.ok(dropped.some((item) => item.kind === "user" && item.reason === "filtered"));
  assert.ok(payloads.some((payload) => payload.message_kind === "assistant_step"));
});

test("a task or injected origin contributes no user_query but the assistant work is kept", () => {
  const turn = turnFrom([
    metadata(),
    turnPrompt({ turnId: 0, text: "subagent system prompt", origin: { kind: "task" } }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "subagent result" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  const { payloads, dropped } = mapTurnToMessages(turn, CFG);
  assert.equal(payloads.some((payload) => payload.message_kind === "user_query"), false);
  assert.equal(payloads.filter((payload) => payload.role === "assistant").length, 1);
  assert.deepEqual(dropped.filter((item) => item.kind === "user").map((item) => item.reason), ["task"]);
});

test("two turns with identical text are two messages, because they are two questions", () => {
  const entries = [
    metadata(),
    turnPrompt({ turnId: 0, text: "same question" }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "answer one" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
    turnPrompt({ turnId: 1, text: "same question" }),
    stepBegin({ turnId: 1, step: 1 }),
    contentPart({ turnId: 1, step: 1, part: { type: "text", text: "answer two" } }),
    stepEnd({ turnId: 1, step: 1 }),
    turnEnded({ turnId: 1 }),
  ].map((record, index) => ({ line: index + 1, record }));
  const projection = projectWire(entries);
  const mapped = projection.turns.flatMap((turn) => mapTurnToMessages(turn, CFG));
  const users = mapped.flatMap((item) => item.payloads).filter((payload) => payload.message_kind === "user_query");
  assert.equal(users.length, 2);
  assert.deepEqual(users.map((payload) => payload.parts[0].text), ["same question", "same question"]);
  // Stable ids differ: the identity is the turn, not the text.
  assert.notEqual(mapped[0].stableIds[0], mapped[1].stableIds[0]);
});

test("capture filters drop a whole turn, including its tool parts", () => {
  const turn = turnFrom([
    metadata(),
    turnPrompt({ turnId: 0, text: "SECRET-TOKEN-abcdef" }),
    stepBegin({ turnId: 0, step: 1 }),
    toolCall({ turnId: 0, step: 1, toolCallId: "call-3", name: "Read", args: { file_path: "/tmp/a" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  // The shared rule syntax is sed-style: `d/pattern/` drops a matching turn.
  const cfg = { ...CFG, captureFilters: ["d/SECRET-TOKEN-/"] };
  const { payloads } = mapTurnToMessages(turn, cfg);
  assert.equal(payloads.length, 0);
});

test("a message made only of tool parts survives the shared filter", () => {
  const turn = turnFrom([
    metadata(),
    turnPrompt({ turnId: 0, text: "read something for me" }),
    stepBegin({ turnId: 0, step: 1 }),
    toolCall({ turnId: 0, step: 1, toolCallId: "call-4", name: "Glob", args: { pattern: "**/*.md" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  const { payloads } = mapTurnToMessages(turn, CFG);
  const assistant = payloads.find((payload) => payload.role === "assistant");
  assert.ok(assistant);
  assert.deepEqual(assistant.parts.map((part) => part.type), ["tool"]);
});

test("scalar tool arguments are wrapped so tool_input stays an object", () => {
  const turn = turnFrom([
    metadata(),
    turnPrompt({ turnId: 0, text: "run a command" }),
    stepBegin({ turnId: 0, step: 1 }),
    toolCall({ turnId: 0, step: 1, toolCallId: "call-5", name: "Bash", args: "ls -la" }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  const { payloads } = mapTurnToMessages(turn, CFG);
  const part = payloads.find((payload) => payload.role === "assistant").parts[0];
  assert.equal(typeof part.tool_input, "object");
  assert.deepEqual(part.tool_input, { value: "ls -la" });
});

test("stable ids are wire facts, not text hashes", () => {
  const records = [
    metadata(),
    turnPrompt({ turnId: 4, promptId: "prompt-x", text: "how do I build this?" }),
    stepBegin({ turnId: 4, step: 1 }),
    contentPart({ turnId: 4, step: 1, part: { type: "text", text: "Run the tests first." } }),
    stepEnd({ turnId: 4, step: 1 }),
    turnEnded({ turnId: 4 }),
  ];
  const first = mapTurnToMessages(turnFrom(records), CFG);
  // The same records projected again produce the same ids, which is what makes
  // a re-read safe.
  const second = mapTurnToMessages(turnFrom(records), CFG);
  assert.deepEqual(first.stableIds, second.stableIds);
  assert.equal(first.stableIds[0], "t4:s0:mprompt-x:user:0");
});

test("the shared shaper's drop rules reach Kimi messages", () => {
  const withText = (text, turnId = 0) => turnFrom([
    metadata(),
    turnPrompt({ turnId, text }),
    stepBegin({ turnId, step: 1 }),
    contentPart({ turnId, step: 1, part: { type: "text", text: "A real answer that is long enough." } }),
    stepEnd({ turnId, step: 1 }),
    turnEnded({ turnId }),
  ]);

  // An acknowledgement is not a question.
  assert.deepEqual(mapTurnToMessages(withText("ok"), CFG).payloads.filter((p) => p.message_kind === "user_query"), []);
  // Nor is a slash command typed into the prompt.
  assert.deepEqual(mapTurnToMessages(withText("/clear"), CFG).payloads.filter((p) => p.message_kind === "user_query"), []);
  // Nor a two-character noise turn.
  assert.deepEqual(mapTurnToMessages(withText("hm"), CFG).payloads.filter((p) => p.message_kind === "user_query"), []);
  // A real question survives.
  assert.equal(mapTurnToMessages(withText("how do I deploy this?"), CFG).payloads.filter((p) => p.message_kind === "user_query").length, 1);
});

test("captureMaxLength caps the text the server receives", () => {
  const long = `how do I ${"x".repeat(5000)}`;
  const turn = turnFrom([
    metadata(),
    turnPrompt({ turnId: 0, text: long }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "A real answer that is long enough." } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  const { payloads } = mapTurnToMessages(turn, { ...CFG, captureMaxLength: 200 });
  const user = payloads.find((p) => p.message_kind === "user_query");
  assert.ok(user, "the turn is still captured");
  assert.ok(user.parts[0].text.length <= 200, `expected a capped text, got ${user.parts[0].text.length}`);
  assert.match(user.parts[0].text, /\[truncated\]$/);
});

test("capture filters drop a matching message through the shared rules", () => {
  const turn = turnFrom([
    metadata(),
    turnPrompt({ turnId: 0, text: "my token is SECRET-VALUE-1234" }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "Noted, I will keep it safe." } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  const { payloads } = mapTurnToMessages(turn, { ...CFG, captureFilters: ["d/SECRET-VALUE/"] });
  assert.equal(payloads.some((p) => p.message_kind === "user_query"), false);
});

test("a tool-only step keeps its tool parts and gains no synthesized text", () => {
  const turn = turnFrom([
    metadata(),
    turnPrompt({ turnId: 0, text: "read the config for me" }),
    stepBegin({ turnId: 0, step: 1 }),
    toolCall({ turnId: 0, step: 1, toolCallId: "c9", name: "Read", args: { file_path: "/tmp/config.json" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  const { payloads } = mapTurnToMessages(turn, CFG);
  const assistant = payloads.find((p) => p.role === "assistant");
  assert.deepEqual(assistant.parts.map((part) => part.type), ["tool"]);
  assert.equal(assistant.parts[0].tool_id, "c9");
});

test("a text part and a tool part keep their original order", () => {
  const turn = turnFrom([
    metadata(),
    turnPrompt({ turnId: 0, text: "check the config first" }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "Let me look at that file." } }),
    toolCall({ turnId: 0, step: 1, toolCallId: "c8", name: "Read", args: { file_path: "/tmp/a" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  const assistant = mapTurnToMessages(turn, CFG).payloads.find((p) => p.role === "assistant");
  assert.deepEqual(assistant.parts.map((part) => part.type), ["text", "tool"]);
});

test("a captured list gets its own block when it does not start at 1", () => {
  const text = "**经济与民生**\n8. 消费数据\n9. 房价";
  const turn = turnFrom([
    metadata(),
    turnPrompt({ turnId: 0, text: "看下热榜" }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  const assistant = mapTurnToMessages(turn, CFG).payloads.find((p) => p.role === "assistant");
  assert.equal(assistant.parts[0].text, "**经济与民生**\n\n8. 消费数据\n9. 房价");
});

test("a list already in its own block is captured unchanged", () => {
  const text = "导语\n\n1. 一\n```\n1. 二\n```";
  const turn = turnFrom([
    metadata(),
    turnPrompt({ turnId: 0, text: "看下清单" }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  const assistant = mapTurnToMessages(turn, CFG).payloads.find((p) => p.role === "assistant");
  assert.equal(assistant.parts[0].text, text);
});

test("the host's hook output is not captured as the user's words", () => {
  const text = '<hook_result hook_event="UserPromptSubmit">\nRelevant memory from OpenViking.\n<memory uri="viking://user/default/memories/events/2026/11/17" type="events" score="0.74" detail="uri" />\n</hook_result>\n清理测试残留';
  const turn = turnFrom([
    metadata(),
    turnPrompt({ turnId: 0, text }),
    stepBegin({ turnId: 0, step: 1 }),
    contentPart({ turnId: 0, step: 1, part: { type: "text", text: "好" } }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  const { payloads } = mapTurnToMessages(turn, CFG);
  assert.equal(payloads[0].parts[0].text, "清理测试残留");
});

test("a turn carrying nothing but the host's hook output is not captured", () => {
  const text = '<hook_result hook_event="UserPromptSubmit">\n<memory uri="viking://user/default/memories/events/2026/11/17" type="events" score="0.74" detail="uri" />\n</hook_result>';
  const turn = turnFrom([
    metadata(),
    turnPrompt({ turnId: 0, text }),
    stepEnd({ turnId: 0, step: 1 }),
    turnEnded({ turnId: 0 }),
  ]);
  const { payloads } = mapTurnToMessages(turn, CFG);
  assert.equal(payloads.filter((payload) => payload.role === "user").length, 0);
});
