import assert from "node:assert/strict";
import test from "node:test";
import { captureEvent, promptText } from "./capture.mjs";

const CONFIG = {
  captureAssistantTurns: true,
  captureToolResults: true,
  captureToolMaxChars: 1000000,
  captureMaxLength: 24000,
  captureMode: "semantic",
  peerId: "workspace-a",
};

test("captures DSH message events without recapturing injected context", () => {
  const user = captureEvent({
    type: "user/message",
    data: {
      role: "user",
      content: [{ type: "text", text: "Remember that deployment uses blue." }],
      source: { kind: "user" },
    },
  }, CONFIG);
  assert.deepEqual(user, {
    role: "user",
    parts: [{ type: "text", text: "Remember that deployment uses blue." }],
    peer_id: "workspace-a",
  });

  const injected = captureEvent({
    type: "user/message",
    data: {
      role: "user",
      content: [{ type: "text", text: "<openviking-context>blue</openviking-context>" }],
      source: { kind: "plugin", plugin: "openviking-memory", form: "recall" },
    },
  }, CONFIG);
  assert.equal(injected, null);

  // Every plugin's injections stay out of memory, not just this plugin's:
  // synthetic context mirrored as human input would poison extraction.
  const otherPlugin = captureEvent({
    type: "user/message",
    data: {
      role: "user",
      content: [{ type: "text", text: "Time sampled while preparing turn 3" }],
      source: { kind: "plugin", plugin: "time-context", form: "snapshot" },
    },
  }, CONFIG);
  assert.equal(otherPlugin, null);
});

test("preserves DSH tool call identity in captured tool results", () => {
  const names = new Map();
  assert.equal(captureEvent({
    type: "tool/call",
    data: { callId: "call-1", name: "bash", arguments: "{\"command\":\"pwd\"}" },
  }, CONFIG, names), null);

  const captured = captureEvent({
    type: "tool/result",
    data: {
      message: {
        role: "user",
        content: [{
          type: "tool-result",
          toolCallId: "call-1",
          content: [{ type: "text", text: "/workspace" }],
        }],
        source: { kind: "tool", callId: "call-1" },
      },
    },
  }, CONFIG, names);

  assert.equal(captured.role, "user");
  assert.equal(captured.parts[0].type, "tool");
  assert.equal(captured.parts[0].tool_id, "call-1");
  assert.equal(captured.parts[0].tool_name, "bash");
  assert.match(captured.parts[0].tool_output, /workspace/);
  assert.equal(names.size, 0);
});

test("does not retain tool call names when tool-result capture is disabled", () => {
  const names = new Map();
  const config = { ...CONFIG, captureToolResults: false };
  captureEvent({
    type: "tool/call",
    data: { callId: "call-disabled", name: "bash" },
  }, config, names);
  assert.equal(names.size, 0);
});

test("releases tool call names even when a tool result has no capturable content", () => {
  const names = new Map([["call-empty", "bash"]]);
  const captured = captureEvent({
    type: "tool/result",
    data: {
      message: {
        role: "user",
        content: [],
        source: { kind: "tool", callId: "call-empty" },
      },
    },
  }, CONFIG, names);

  assert.equal(captured, null);
  assert.equal(names.size, 0);
});

test("preserves DSH event time so identical offline messages do not deduplicate", () => {
  const captured = captureEvent({
    type: "user/message",
    time: 1_786_681_234_567,
    data: {
      role: "user",
      content: [{ type: "text", text: "Repeat this exact fact." }],
      source: { kind: "user" },
    },
  }, CONFIG);

  assert.equal(captured.created_at, "2026-08-14T04:20:34.567Z");
});

test("builds recall queries from current input while excluding its own context", () => {
  assert.equal(promptText([
    {
      role: "user",
      content: [{ type: "text", text: "Current question" }],
      source: { kind: "user" },
    },
    {
      role: "user",
      content: [{ type: "text", text: "old recall" }],
      source: { kind: "plugin", plugin: "openviking-memory" },
    },
    {
      role: "user",
      content: [{ type: "text", text: "background job completed" }],
      source: { kind: "plugin", plugin: "job-controller", form: "notice", summary: "done" },
    },
  ]), "Current question\n\nbackground job completed");
});
