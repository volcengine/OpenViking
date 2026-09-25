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

test("mixed text and tool capture does not duplicate rendered tool calls", () => {
  const { payloads } = extractBranchCapturePayloads([{
    role: "assistant",
    content: [
      { type: "text", text: "Run this." },
      { type: "toolCall", id: "call-1", name: "lookup", arguments: { q: "x" } },
    ],
  }], 0, { faithfulCapture: true });
  assert.equal(payloads[0].parts[0].text, "Run this.");
  assert.equal(payloads[0].parts[1].tool_name, "lookup");
});

for (const [mode, modeConfig] of [
  ["normal", {}],
  ["takeover", { takeoverEnabled: true }],
  ["faithful", { faithfulCapture: true }],
]) {
  for (const [name, rules, text, expected] of [
    ["substitution", ["s/secret/[removed]/g"], "Remember secret for later.", "Remember [removed] for later."],
    ["drop", ["d/secret/"], "Remember secret for later.", null],
    ["keep match", ["k/approved/"], "Remember approved settings.", "Remember approved settings."],
    ["keep miss", ["k/approved/"], "Remember other settings.", null],
    ["empty substitution", ["s/^.*$//"], "Remember secret for later.", null],
    ["single substitution", ["s/secret/secret-safe/"], "Remember secret for later.", "Remember secret-safe for later."],
    ["ordered rules", ["s/secret/blocked/", "d/blocked/"], "Remember secret for later.", null],
    ["filter after sanitation", ["d/secret/"], "<openviking-context>secret</openviking-context>Remember safe settings.", "Remember safe settings."],
    ["filter before truncation", ["d/secret/"], `${"Safe settings. ".repeat(10)}secret`, null],
  ]) {
    for (const fallback of [false, true]) {
      test(`${mode} capture applies ${name} to ${fallback ? "fallback content" : "text parts"}`, () => {
        const branch = [{ role: "user", content: fallback ? [text] : text }];
        const original = structuredClone(branch);
        const result = extractBranchCapturePayloads(branch, 0, {
          ...modeConfig, captureFilters: rules, captureMaxLength: 80, peerId: "peer-filter",
        });
        assert.deepEqual(result.payloads, expected === null ? [] : [{
          role: "user",
          ...(fallback ? { content: expected } : { parts: [{ type: "text", text: expected }] }),
          peer_id: "peer-filter",
        }]);
        assert.equal(result.nextEntryCount, 1);
        assert.deepEqual(branch, original);
        assert.deepEqual(extractBranchCapturePayloads(branch, 1, modeConfig).payloads, []);
      });
    }
  }

  for (const scope of ["user", "assistant"]) {
    test(`${mode} capture applies ${scope}-scoped rules only to that role`, () => {
      const result = extractBranchCapturePayloads(["USER", "ASSISTANT"].map((role) => ({
        role, content: "Remember secret for later.",
      })), 0, { ...modeConfig, captureFilters: [`${scope}:s/secret/[removed]/`] });
      assert.deepEqual(result.payloads, ["user", "assistant"].map((role) => ({
        role,
        parts: [{ type: "text", text: `Remember ${role === scope ? "[removed]" : "secret"} for later.` }],
      })));
    });
  }

  const toolCall = { type: "toolCall", id: "call-filter", name: "lookup", arguments: { value: "secret" } };
  const toolPart = {
    type: "tool", tool_id: "call-filter", tool_name: "lookup",
    tool_status: "running", tool_input: { value: "secret" },
  };
  for (const [name, rules, texts, expected] of [
    ["whole-turn drop", ["d/secret/"], ["Remember secret for later."], null],
    ["whole-turn keep miss", ["k/approved/"], ["Remember other settings."], null],
    ["conversation-only matching", ["d/secret/"], ["Remember safe settings."], "Remember safe settings."],
    ["conversation-only substitution", ["s/secret/secret-safe/"], ["Remember secret for later."], "Remember secret-safe for later."],
    ["aggregate keep", ["k/approved/"], ["Remember approved settings.", "Keep this detail too."], "Remember approved settings.\n\nKeep this detail too."],
    ["empty text removal", ["s/^.*$//s"], ["Remember secret for later."], ""],
  ]) {
    test(`${mode} capture uses ${name} for mixed text and tools`, () => {
      const branch = [{ role: "assistant", content: [
        ...texts.map((text) => ({ type: "text", text })), toolCall,
      ] }];
      const original = structuredClone(branch);
      const { payloads } = extractBranchCapturePayloads(branch, 0, { ...modeConfig, captureFilters: rules });
      assert.deepEqual(payloads, expected === null ? [] : [{
        role: "assistant",
        parts: [...(expected ? [{ type: "text", text: expected }] : []), toolPart],
      }]);
      assert.deepEqual(branch, original);
    });
  }

  test(`${mode} capture preserves tool-only input and output with conversation rules`, () => {
    const branch = [
      { role: "assistant", content: [toolCall] },
      { role: "toolResult", toolCallId: "call-filter", toolName: "lookup", content: "secret" },
    ];
    const { payloads } = extractBranchCapturePayloads(branch, 0, {
      ...modeConfig, captureFilters: ["s/secret/[removed]/", "d/secret/", "k/approved/"],
    });
    assert.deepEqual(payloads, [
      { role: "assistant", parts: [toolPart] },
      { role: "user", parts: [{
        type: "tool", tool_id: "call-filter", tool_name: "lookup",
        tool_status: "completed", tool_output: "secret",
      }] },
    ]);
  });

  for (const role of ["user", "assistant"]) {
    test(`${mode} capture preserves exact ${role} output when no valid filter applies`, () => {
      const branch = [
        { role, content: "  Remember these settings.  " },
        { role, content: ["Remember this fallback."] },
        { role, content: [{ type: "text", text: "Remember tool settings." }, toolCall] },
        { role, content: [toolCall] },
        ...["ok", "hi", "!!!", "/viking", "[OpenViking-memory] synced"].map((content) => ({ role, content })),
      ];
      const baseline = extractBranchCapturePayloads(branch, 0, modeConfig);
      for (const captureFilters of [undefined, [], ["invalid", "s/[//", 42], [`${role === "user" ? "assistant" : "user"}:d/.*/`]]) {
        assert.deepEqual(extractBranchCapturePayloads(branch, 0, { ...modeConfig, captureFilters }), baseline);
      }
    });
  }

  test(`${mode} capture preserves its short-turn policy after filtering`, () => {
    const { payloads } = extractBranchCapturePayloads(["ok", "hi", "!!!"].map((content) => ({
      role: "user", content: `prefix ${content}`,
    })), 0, { ...modeConfig, captureFilters: ["s/^prefix //"] });
    assert.deepEqual(payloads, mode === "normal" ? [] : ["ok", "hi", "!!!"].map((text) => ({
      role: "user", parts: [{ type: "text", text }],
    })));
  });

  test(`${mode} capture excludes commands and status after filtering`, () => {
    const { payloads } = extractBranchCapturePayloads([
      { role: "user", content: "prefix /viking" },
      { role: "assistant", content: "prefix [OpenViking-memory] synced" },
    ], 0, { ...modeConfig, captureFilters: ["s/^prefix //"] });
    assert.deepEqual(payloads, []);
  });

  test(`${mode} capture cannot satisfy keep with removed context`, () => {
    const branch = [{ role: "user", content: "<openviking-context>approved</openviking-context>Remember private details." }];
    const result = extractBranchCapturePayloads(branch, 0, {
      ...modeConfig, captureFilters: ["k/approved/"],
    });
    assert.deepEqual(result.payloads, []);
    assert.equal(result.nextEntryCount, 1);
  });
}
