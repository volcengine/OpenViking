/**
 * Decoder tests for the CodeBuddy transcript reader.
 *
 * These run against synthetic records. The cases that matter are the ones real
 * transcripts taught us: host blocks around the ask, a quoted earlier turn that
 * must not be mistaken for the current one, host-authored records that carry no
 * human text, and tool calls that are separate records from the messages they
 * belong to.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  extractCaptureTurns,
  parseTranscript,
  stripHostBlocks,
  userTurnText,
} from "./cb-transcript.mjs";

const message = (role, text, extra = {}) => ({
  type: "message",
  role,
  content: [{ type: role === "user" ? "input_text" : "output_text", text }],
  ...extra,
});

test("userTurnText takes the ask out of its host wrapper", () => {
  assert.equal(
    userTurnText("<system-reminder>noise</system-reminder>\n<user_query>真实问题</user_query>"),
    "真实问题",
  );
});

test("userTurnText is not fooled by a quoted earlier turn", () => {
  // <previous_user_message> wraps a whole <user_query>; only the unquoted one
  // is the current ask.
  assert.equal(
    userTurnText(
      "<previous_user_message><user_query>旧问题</user_query></previous_user_message>"
      + "<user_query>新问题</user_query>",
    ),
    "新问题",
  );
});

test("userTurnText falls back to the stripped text when there is no wrapper", () => {
  // Measured: most real user records carry no <user_query> at all.
  assert.equal(userTurnText("裸文本"), "裸文本");
  assert.equal(
    userTurnText("<memory_and_skills_reminder>x</memory_and_skills_reminder>人写的问题"),
    "人写的问题",
  );
});

test("userTurnText is empty when the record carries only host blocks", () => {
  assert.equal(userTurnText("<cb_summary>压缩摘要</cb_summary><system-reminder>x</system-reminder>"), "");
  assert.equal(userTurnText(""), "");
});

test("userTurnText prefers the longest candidate when several appear", () => {
  assert.equal(
    userTurnText("<user_query>短</user_query><user_query>这是一个更长的候选</user_query>"),
    "这是一个更长的候选",
  );
});

test("stripHostBlocks removes wrappers but keeps surrounding text", () => {
  assert.equal(stripHostBlocks("<identity_context>me</identity_context>问题"), "问题");
});

test("extractCaptureTurns assembles turns from separate records", () => {
  const turns = extractCaptureTurns([
    message("user", "<user_query>帮我看看 A</user_query>"),
    { type: "reasoning", content: "推理不成轮" },
    message("assistant", "好的"),
    { type: "function_call", name: "Bash", arguments: '{"command":"ls"}', callId: "call_1" },
    { type: "function_call_result", name: "Bash", callId: "call_1", status: "success", output: "a.txt" },
    { type: "file-history-snapshot" },
    message("user", "<user_query>那 B 呢</user_query>"),
  ]);

  assert.equal(turns.length, 3);
  assert.deepEqual(turns.map((t) => t.role), ["user", "assistant", "user"]);
  assert.equal(turns[0].text, "帮我看看 A");

  // The assistant turn kept its prose and both tool parts, in order.
  assert.deepEqual(turns[1].parts.map((p) => p.type), ["text", "tool", "tool"]);
  assert.deepEqual(
    turns[1].parts.slice(1).map((p) => [p.tool_name, p.tool_status]),
    [["Bash", "running"], ["Bash", "completed"]],
  );
  assert.deepEqual(turns[1].parts[1].tool_input, { command: "ls" });
});

test("extractCaptureTurns treats a lone tool record as an assistant turn", () => {
  const turns = extractCaptureTurns([
    { type: "function_call", name: "Read", arguments: "{}", callId: "c9" },
  ]);
  assert.deepEqual(turns.map((t) => [t.role, t.parts.length]), [["assistant", 1]]);
});

test("extractCaptureTurns drops host-authored user records", () => {
  // isMeta marks task notifications and "continue" nudges; skipRun marks slash
  // commands and the command caveat. Neither is a human ask.
  assert.equal(
    extractCaptureTurns([message("user", "随便什么", { providerData: { isMeta: true } })]).length,
    0,
  );
  assert.equal(
    extractCaptureTurns([message("user", "随便什么", { providerData: { skipRun: true } })]).length,
    0,
  );
});

test("extractCaptureTurns drops a user record that carries only host blocks", () => {
  assert.equal(
    extractCaptureTurns([message("user", "<system-reminder>only noise</system-reminder>")]).length,
    0,
  );
});

test("a tool result with a non-success status is reported as not completed", () => {
  const turns = extractCaptureTurns([
    message("assistant", "trying"),
    { type: "function_call_result", name: "Bash", callId: "c1", status: "error", output: "boom" },
  ]);
  const tool = turns[0].parts.find((p) => p.type === "tool");
  assert.equal(tool.tool_status, "error");
});

test("parseTranscript accepts JSONL and a whole-file JSON array", () => {
  assert.equal(parseTranscript('{"type":"message"}\n{"type":"message"}').length, 2);
  assert.equal(parseTranscript('[{"type":"message"}]').length, 1);
  assert.equal(parseTranscript("not json\n\n").length, 0);
});
