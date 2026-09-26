import test from "node:test";
import assert from "node:assert/strict";
import { extractBranchCapturePayloads } from "../lib/capture-adapter.mjs";

test("extractBranchCapturePayloads converts user text to message payload", () => {
  const branch = [
    { type: "message", message: { role: "user", content: "Remember this decision for later." } },
  ];

  const result = extractBranchCapturePayloads(branch, 0, { peerId: "peer-a" });
  assert.equal(result.nextEntryCount, 1);
  assert.equal(result.payloads.length, 1);
  assert.equal(result.payloads[0].role, "user");
  assert.equal(result.payloads[0].parts[0].type, "text");
  assert.match(result.payloads[0].parts[0].text, /Remember this decision/);
  assert.equal(result.payloads[0].peer_id, "peer-a");
});

test("extractBranchCapturePayloads emits structured tool parts", () => {
  const branch = [
    {
      type: "message",
      message: {
        role: "assistant",
        content: [
          { type: "text", text: "I will inspect it." },
          { type: "tool_call", id: "call-1", name: "read", input: { path: "a.txt" } },
        ],
      },
    },
  ];

  const result = extractBranchCapturePayloads(branch, 0, {
    captureAssistantTurns: true,
    captureToolMaxChars: 2000,
  });
  assert.equal(result.payloads.length, 1);
  assert.equal(result.payloads[0].role, "assistant");
  assert.ok(Array.isArray(result.payloads[0].parts));
  assert.equal(result.payloads[0].parts.some((part) => part.type === "tool" && part.tool_name === "read"), true);
});

test("extractBranchCapturePayloads resets watermark when branch shrinks", () => {
  const result = extractBranchCapturePayloads([
    { type: "message", message: { role: "user", content: "New compacted branch content." } },
  ], 5, {});

  assert.equal(result.resetWatermark, true);
  assert.equal(result.nextEntryCount, 1);
  assert.equal(result.payloads.length, 1);
});

test("capture is faithful without asking: ack, short, and punctuation turns are kept", () => {
  const branch = [
    { type: "message", message: { role: "user", content: "ok" } },
    { type: "message", message: { role: "user", content: "hi" } },
    { type: "message", message: { role: "user", content: "!!!" } },
  ];

  const result = extractBranchCapturePayloads(branch, 0, {});
  assert.equal(result.payloads.length, 3);
  assert.deepEqual(result.payloads.map((p) => p.parts[0].text), ["ok", "hi", "!!!"]);
});

test("faithful capture still skips commands and plugin status", () => {
  const result = extractBranchCapturePayloads([
    { type: "message", message: { role: "user", content: "/viking" } },
    { type: "message", message: { role: "assistant", content: "[OpenViking-memory] synced" } },
  ], 0, {});

  assert.equal(result.payloads.length, 0);
});

test("pi toolResult messages are captured as prefixed user turns", () => {
  const branch = [
    {
      type: "message",
      message: {
        role: "toolResult",
        toolCallId: "call-7",
        toolName: "read",
        content: "line one\nline two",
      },
    },
  ];

  const { payloads } = extractBranchCapturePayloads(branch, 0, {
    captureToolResults: true,
    captureToolMaxChars: 1000,
    peerId: "peer-a",
  });
  assert.equal(payloads.length, 1);
  assert.equal(payloads[0].role, "user");
  assert.equal(payloads[0].peer_id, "peer-a");
  assert.equal(payloads[0].parts[0].type, "text");
  assert.match(payloads[0].parts[0].text, /^\[tool-result read\] /);
  assert.match(payloads[0].parts[0].text, /line two/);
});

test("toolResult roles are recognised in every spelling pi and providers use", () => {
  for (const role of ["toolResult", "toolresult", "tool_result", "tool"]) {
    const { payloads } = extractBranchCapturePayloads(
      [{ type: "message", message: { role, toolName: "bash", content: "exit 0" } }],
      0,
      { captureToolResults: true, captureToolMaxChars: 1000 },
    );
    assert.equal(payloads.length, 1, `role ${role} was dropped`);
    assert.match(payloads[0].parts[0].text, /^\[tool-result bash\] exit 0/);
  }
});

test("toolResult capture is dropped when captureToolResults is false", () => {
  const { payloads } = extractBranchCapturePayloads(
    [
      { type: "message", message: { role: "user", content: "run it" } },
      { type: "message", message: { role: "toolResult", toolName: "bash", content: "exit 0" } },
    ],
    0,
    { captureToolResults: false },
  );
  assert.equal(payloads.length, 1);
  assert.equal(payloads[0].role, "user");
  assert.match(payloads[0].parts[0].text, /run it/);
});

test("toolResult text is truncated to captureToolMaxChars", () => {
  const { payloads } = extractBranchCapturePayloads(
    [{ type: "message", message: { role: "toolResult", toolName: "read", content: "z".repeat(5000) } }],
    0,
    { captureToolResults: true, captureToolMaxChars: 400 },
  );
  assert.equal(payloads.length, 1);
  const text = payloads[0].parts[0].text;
  assert.ok(text.length < 500, `expected truncation, got ${text.length} chars`);
  assert.match(text, /\[truncated\]$/);
});

test("tool-only payloads carry tool output once, not duplicated as text", () => {
  const output = "z".repeat(5000);
  const { payloads } = extractBranchCapturePayloads(
    [
      {
        type: "message",
        message: {
          role: "assistant",
          content: [
            { type: "tool_result", tool_use_id: "call-dup-check", output },
          ],
        },
      },
    ],
    0,
    { captureAssistantTurns: true, captureToolMaxChars: 1000000 },
  );

  const parts = payloads.flatMap((payload) => payload.parts || []);
  assert.equal(parts.filter((part) => part.type === "text").length, 0);
  const toolParts = parts.filter((part) => part.type === "tool");
  assert.equal(toolParts.length, 1);
  assert.equal(toolParts[0].tool_output, output);
});
