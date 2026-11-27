/**
 * Decoder tests for the CodeBuddy transcript reader.
 *
 * These run against synthetic records. The cases that matter are the ones real
 * transcripts taught us: host blocks around the ask, a quoted earlier turn that
 * must not be mistaken for the current one, host-authored records that carry no
 * human text, and tool calls that are separate records from the messages they
 * belong to. The incremental cases pin the cursor the dispatcher relies on.
 */

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  HOST_BLOCK_TAGS,
  buildCodebuddyTurns,
  extractCodebuddyTurns,
  extractUnseenCodebuddyTurns,
  parseTranscript,
  stripHostBlocks,
  userTurnText,
} from "../hosts/codebuddy-turns.mjs";
import { buildIncrementalCapturePlan } from "../hosts/incremental-turn-capture.mjs";

const message = (role, text, extra = {}) => ({
  type: "message",
  role,
  content: [{ type: role === "user" ? "input_text" : "output_text", text }],
  ...extra,
});

const call = (name, args, callId) => ({ type: "function_call", name, arguments: args, callId });
const result = (name, callId, status = "success", output = "out") => (
  { type: "function_call_result", name, callId, status, output }
);
const messagesOf = (records) => buildIncrementalCapturePlan(extractCodebuddyTurns(records)).payloads;
const toolSummary = (message) => message.parts
  .filter((part) => part.type === "tool")
  .map((part) => [part.tool_name, part.tool_id, part.tool_status]);

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

test("extractCodebuddyTurns assembles turns from separate records", () => {
  const turns = extractCodebuddyTurns([
    message("user", "<user_query>帮我看看 A</user_query>"),
    { type: "reasoning", content: "推理不成轮" },
    message("assistant", "好的"),
    call("Bash", '{"command":"ls"}', "call_1"),
    result("Bash", "call_1"),
    { type: "file-history-snapshot" },
    message("user", "<user_query>那 B 呢</user_query>"),
  ]);

  assert.equal(turns.length, 3);
  assert.deepEqual(turns.map((t) => t.role), ["user", "assistant", "user"]);
  // The turn id is the index of the record that opened it — the append-only
  // position the incremental cursor relies on.
  assert.deepEqual(turns.map((t) => t.turnId), ["0", "2", "6"]);
  assert.equal(turns[0].content, "帮我看看 A");
  assert.equal(turns[1].content, "好的");
  assert.deepEqual(turns[1].payload.content.map((block) => block.type), ["text", "function_call", "tool_result"]);
});

test("a folded tool call and its result go out as structured parts", () => {
  const [sent] = messagesOf([
    message("assistant", "先列一下目录"),
    call("Bash", '{"command":"ls"}', "call_1"),
    result("Bash", "call_1", "success", "a.txt"),
  ]);
  assert.equal(sent.parts[0].text, "先列一下目录");
  assert.deepEqual(toolSummary(sent), [["Bash", "call_1", "running"], ["Bash", "call_1", "completed"]]);
  assert.deepEqual(sent.parts[1].tool_input, { command: "ls" });
  assert.equal(sent.parts[2].tool_output, "a.txt");
});

test("extractCodebuddyTurns treats a lone tool record as an assistant turn", () => {
  const messages = messagesOf([call("Read", "{}", "c9")]);
  assert.deepEqual(messages.map((m) => [m.role, toolSummary(m)]), [["assistant", [["Read", "c9", "running"]]]]);
});

test("extractCodebuddyTurns gives tool use after a user message to the assistant", () => {
  // The model may call a tool before it writes any text; that activity must
  // not be captured as part of the user's turn.
  const turns = extractCodebuddyTurns([
    message("user", "<user_query>列一下文件</user_query>"),
    call("Bash", '{"command":"ls"}', "call_1"),
    result("Bash", "call_1"),
    message("assistant", "有三个文件"),
  ]);

  assert.deepEqual(turns.map((t) => t.role), ["user", "assistant", "assistant"]);
  assert.deepEqual(turns.map((t) => t.turnId), ["0", "1", "3"]);
  assert.equal(turns[0].content, "列一下文件");
  assert.deepEqual(turns[1].payload.content.map((block) => block.type), ["function_call", "tool_result"]);
  assert.equal(turns[2].content, "有三个文件");
});

test("extractCodebuddyTurns drops host-authored user records", () => {
  // isMeta marks task notifications and "continue" nudges; skipRun marks slash
  // commands and the command caveat. Neither is a human ask.
  assert.equal(
    extractCodebuddyTurns([message("user", "随便什么", { providerData: { isMeta: true } })]).length,
    0,
  );
  assert.equal(
    extractCodebuddyTurns([message("user", "随便什么", { providerData: { skipRun: true } })]).length,
    0,
  );
});

test("extractCodebuddyTurns drops a user record that carries only host blocks", () => {
  assert.equal(
    extractCodebuddyTurns([message("user", "<system-reminder>only noise</system-reminder>")]).length,
    0,
  );
});

test("a tool result with a non-success status goes out as an error part", () => {
  const [sent] = messagesOf([
    message("assistant", "trying"),
    result("Bash", "c1", "error", "boom"),
  ]);
  assert.equal(sent.parts[0].text, "trying");
  assert.deepEqual(toolSummary(sent), [["Bash", "c1", "error"]]);
  assert.equal(sent.parts[1].tool_output, "boom");
});

test("parseTranscript reads JSONL and nothing else", () => {
  assert.equal(parseTranscript('{"type":"message"}\n{"type":"message"}').length, 2);
  assert.equal(parseTranscript("not json\n\n").length, 0);
  // A whole-file JSON array is not a shape the host contract documents; the
  // one-line fallback was removed rather than kept unbacked.
  assert.equal(parseTranscript('[{"type":"message"}]').length, 0);
});

test("extractUnseenCodebuddyTurns slices past the acknowledged cursor", () => {
  const dir = mkdtempSync(join(tmpdir(), "cb-turns-test-"));
  try {
    const transcript = join(dir, "session.jsonl");
    writeFileSync(transcript, [
      message("user", "第一问"),
      message("assistant", "第一答"),
      message("user", "第二问"),
    ].map((r) => JSON.stringify(r)).join("\n") + "\n");

    const all = extractUnseenCodebuddyTurns(transcript, null);
    assert.equal(all.status, "ok");
    assert.deepEqual(all.turns.map((t) => t.turnId), ["0", "1", "2"]);

    const after = extractUnseenCodebuddyTurns(transcript, "1");
    assert.deepEqual(after.turns.map((t) => t.turnId), ["2"]);

    // A cursor the transcript no longer holds replays everything: the
    // dispatcher's acknowledged set, not the cursor, is the dedup guard.
    const unknown = extractUnseenCodebuddyTurns(transcript, "99");
    assert.equal(unknown.turns.length, 3);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("extractUnseenCodebuddyTurns reports a transcript it cannot read", () => {
  const dir = mkdtempSync(join(tmpdir(), "cb-turns-test-"));
  try {
    assert.equal(extractUnseenCodebuddyTurns("", null).status, "missing");
    assert.equal(extractUnseenCodebuddyTurns(join(dir, "gone.jsonl"), null).status, "unreadable");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("buildCodebuddyTurns reads the path the event payload carries", () => {
  const dir = mkdtempSync(join(tmpdir(), "cb-turns-test-"));
  try {
    const transcript = join(dir, "session.jsonl");
    writeFileSync(transcript, JSON.stringify(message("user", "问")) + "\n");
    const seen = buildCodebuddyTurns({ transcript_path: transcript }, {});
    assert.equal(seen.status, "ok");
    assert.equal(seen.turns.length, 1);
    assert.equal(buildCodebuddyTurns({}, {}).status, "missing");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the host block tags stay in step with the server-side workbuddy adapter", () => {
  // The two halves parse the same transcript format; a tag added on one side
  // only must fail here rather than silently diverge.
  const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
  const python = readFileSync(join(repoRoot, "openviking", "ingest", "sources", "workbuddy.py"), "utf8");
  const tuple = python.match(/_HOST_BLOCK_TAGS\s*=\s*\(([^)]*)\)/);
  assert.ok(tuple, "_HOST_BLOCK_TAGS not found in workbuddy.py");
  const serverTags = [...tuple[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(HOST_BLOCK_TAGS, serverTags);
});
