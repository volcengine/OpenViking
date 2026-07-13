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
