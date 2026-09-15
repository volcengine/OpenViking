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

for (const role of ["toolResult", "tool_result", "tool"]) {
  for (const isError of [false, true]) {
    test(`extractBranchCapturePayloads preserves ${role} ${isError ? "error" : "completed"} results`, () => {
      const output = [{ type: "text", text: "OVTOOLMARKER98765\n" }];
      const branch = [
        { type: "message", message: { role: "user", content: "Run the command and report its output." } },
        {
          type: "message",
          message: {
            role: "assistant",
            content: [{
              type: "toolCall",
              id: "call-1",
              name: "bash",
              arguments: { command: "echo OVTOOLMARKER98765" },
            }],
          },
        },
        {
          type: "message",
          message: { role, toolCallId: "call-1", toolName: "bash", content: output, isError },
        },
      ];
      const cfg = { captureAssistantTurns: true, captureToolMaxChars: 2000 };
      const result = extractBranchCapturePayloads(branch, 0, cfg);
      assert.deepEqual(result.payloads.map((payload) => payload.role), ["user", "assistant", "user"]);
      assert.deepEqual(result.payloads[1].parts, [{
        type: "tool",
        tool_id: "call-1",
        tool_name: "bash",
        tool_status: "running",
        tool_input: { command: "echo OVTOOLMARKER98765" },
      }]);
      assert.deepEqual(result.payloads[2].parts, [{
        type: "tool",
        tool_id: "call-1",
        tool_name: "bash",
        tool_status: isError ? "error" : "completed",
        tool_output: JSON.stringify(output),
      }]);

      const incremental = extractBranchCapturePayloads(branch, 2, cfg);
      assert.deepEqual(incremental.payloads, [result.payloads[2]]);
      assert.equal(incremental.nextEntryCount, 3);
      assert.deepEqual(extractBranchCapturePayloads(branch, incremental.nextEntryCount, cfg).payloads, []);
    });
  }
}

test("extractBranchCapturePayloads resets watermark when branch shrinks", () => {
  const result = extractBranchCapturePayloads([
    { type: "message", message: { role: "user", content: "New compacted branch content." } },
  ], 5, {});

  assert.equal(result.resetWatermark, true);
  assert.equal(result.nextEntryCount, 1);
  assert.equal(result.payloads.length, 1);
});

test("extractBranchCapturePayloads faithful mode keeps ack, short, and punctuation turns", () => {
  const branch = [
    { type: "message", message: { role: "user", content: "ok" } },
    { type: "message", message: { role: "user", content: "hi" } },
    { type: "message", message: { role: "user", content: "!!!" } },
  ];

  assert.equal(extractBranchCapturePayloads(branch, 0, {}).payloads.length, 0);

  const result = extractBranchCapturePayloads(branch, 0, { faithfulCapture: true });
  assert.equal(result.payloads.length, 3);
  assert.deepEqual(result.payloads.map((p) => p.parts[0].text), ["ok", "hi", "!!!"]);
});

test("extractBranchCapturePayloads faithful mode still skips commands and plugin status", () => {
  const result = extractBranchCapturePayloads([
    { type: "message", message: { role: "user", content: "/viking" } },
    { type: "message", message: { role: "assistant", content: "[OpenViking-memory] synced" } },
  ], 0, { takeoverEnabled: true });

  assert.equal(result.payloads.length, 0);
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
