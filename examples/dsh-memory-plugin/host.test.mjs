import assert from "node:assert/strict";
import test from "node:test";
import { OPENVIKING_PLUGIN_KIND, promptText } from "./decode.mjs";
import host, { pluginMessage } from "./host.mjs";
import { deriveDshSessionId } from "./session-id.mjs";
import { turnMessages } from "./shared/flow/capture.mjs";

const CFG = { captureToolResults: true, captureAssistantTurns: true, captureToolMaxChars: 100000, captureMaxLength: 24000 };
const user = (text, source = { kind: "user" }) => ({ role: "user", content: [{ type: "text", text }], source });
const decodeEvent = (event, cursor = {}, cfg = CFG) => host.decode({ text: "", cursor, notes: [], payload: { event }, cfg });
const sent = (decoded, cfg = CFG) => turnMessages(decoded, cfg);

test("a user message event is one turn; injected context from any producer is not", () => {
  assert.deepEqual(sent(decodeEvent({ type: "user/message", data: user("Remember blue deploys.") })), [
    { role: "user", parts: [{ type: "text", text: "Remember blue deploys." }] },
  ]);
  for (const source of [
    { kind: OPENVIKING_PLUGIN_KIND, form: "recall" },
    { kind: "plugin", plugin: "time-context" },
    { kind: "time-context" },
  ]) {
    assert.deepEqual(decodeEvent({ type: "user/message", data: user("injected", source) }).turns, [], source.kind);
  }
});

test("a tool call keeps its name in the cursor for the result that follows", () => {
  const call = decodeEvent({ type: "tool/call", data: { callId: "c1", name: "bash" } });
  assert.deepEqual(sent(call), [null]);
  const cursor = call.cursorAfter(1);
  assert.deepEqual(cursor, { toolNames: { c1: "bash" } });

  const result = decodeEvent({
    type: "tool/result",
    data: { message: { role: "user", source: { kind: "tool", callId: "c1" }, content: [{ type: "tool-result", toolCallId: "c1", content: [{ type: "text", text: "/workspace" }] }] } },
  }, cursor);
  const [message] = sent(result);
  assert.equal(message.parts[0].tool_name, "bash");
  assert.deepEqual(result.cursorAfter(1), { toolNames: {} });
});

test("tool results stay out with captureToolResults off", () => {
  const cfg = { ...CFG, captureToolResults: false };
  assert.deepEqual(decodeEvent({ type: "tool/call", data: { callId: "c1", name: "bash" } }, {}, cfg).turns, []);
  assert.deepEqual(decodeEvent({ type: "tool/result", data: { message: { role: "user", source: { kind: "tool" }, content: [] } } }, {}, cfg).turns, []);
});

test("read keeps the Windows-safe session id and marks subagents and busy agents", () => {
  assert.deepEqual(host.read("agent/created", { session_id: "im:a", cwd: "/w", resumed: "resume", idle: false }), {
    sessionId: "im:a",
    session: deriveDshSessionId("im:a"),
    cwd: "/w",
    child: undefined,
    prompt: undefined,
    resumed: "resume",
    defer: true,
  });
  assert.equal(host.read("session/event", { session_id: "plain" }).session, "dsh-plain");
  assert.equal(host.read("session/event", { session_id: "s", subagent: true }).child, "subagent");
  assert.equal(host.read("agent/pre-step", { session_id: "s", prompt: "hi" }).prompt, "hi");
});

test("the prompt is only what the user typed", () => {
  assert.equal(promptText([user("first"), user("ctx", { kind: "time-context" }), { role: "user", source: { kind: "tool" }, content: [] }, user("second")]), "first\n\nsecond");
});

test("emit turns outputs into DSH plugin messages and tool decisions", () => {
  const start = host.emit("agent/created", { context: "profile" });
  assert.equal(start.source.kind, OPENVIKING_PLUGIN_KIND);
  assert.equal(start.source.form, "instructions");
  assert.equal(start.content[0].text, "profile");
  assert.equal(host.emit("agent/pre-step", { context: "recall" }).source.form, "recall");
  assert.equal(host.emit("agent/pre-step", {}), null);
  assert.deepEqual(host.emit("tools/pre-execute", { deny: "no" }), { kind: "deny", reason: "no" });
  const notice = host.emit("tools/post-execute", { notice: "use mcp" });
  assert.equal(notice.source.form, "notice");
  assert.ok(notice.source.summary);
  assert.equal(pluginMessage("x", { form: "y" }).source.plugin, "openviking-memory");
});
