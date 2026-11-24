import assert from "node:assert/strict";
import { mkdtemp, access, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { hasNativeContext } from "../conversation.mjs";
import { nativeOwner, captureDirectory } from "../capture.mjs";

const envelope = '<openviking-context source="session-start">\nSaved user profile\n</openviking-context>';
function wire(protocol, text) {
  return protocol === "gemini" ? { contents: [{ role: "user", parts: [{ text }] }] }
    : protocol === "responses" ? { input: [{ role: "user", content: [{ type: "input_text", text }] }] }
      : { messages: [{ role: "user", content: [{ type: "text", text }] }] };
}
for (const protocol of ["chat", "anthropic", "responses", "gemini"]) {
  test(`${protocol} recognizes complete unquoted memory envelopes`, () => {
    for (const text of [envelope, `${envelope}\nMy question`, `My question\n${envelope}`,
      "<relevant-memories>native memory</relevant-memories>", "<relevant-memory>native memory</relevant-memory>"])
      assert.equal(hasNativeContext(wire(protocol, text), protocol), true);
  });
  test(`${protocol} ignores marker mentions, quoted examples and incomplete envelopes`, () => {
    for (const text of [
      "What does <openviking-context> mean?",
      "<openviking-context>",
      "<openviking-context></openviking-context>",
      `[OpenViking memory context]\nWhat does this old header mean?`,
      `Explain this inline example: ${envelope}`,
      `\`\`\`xml\n${envelope}\n\`\`\``,
      `\`\`\`\`markdown\n\`\`\`xml\n${envelope}\n\`\`\`\n\`\`\`\``,
      `~~~xml\n${envelope}\n~~~`,
      envelope.split("\n").map((line) => `> ${line}`).join("\n"),
      envelope.split("\n").map((line) => `    ${line}`).join("\n"),
    ]) assert.equal(hasNativeContext(wire(protocol, text), protocol), false, text);
    // Structural fields and non-text tool payloads cannot claim native ownership.
    assert.equal(hasNativeContext({ ...wire(protocol, "Normal question"), model: envelope }, protocol), false);
  });
}

test("native system text remains recognized but quoted system documentation does not", () => {
  assert.equal(hasNativeContext({ system: [{ type: "text", text: envelope }] }, "anthropic"), true);
  assert.equal(hasNativeContext({ systemInstruction: { parts: [{ text: envelope }] } }, "gemini"), true);
  assert.equal(hasNativeContext({ instructions: envelope }, "responses"), true);
  assert.equal(hasNativeContext({ messages: [{ role: "system", content: envelope }] }, "chat"), true);
  assert.equal(hasNativeContext({ messages: [{ role: "system", content: `Documentation:\n\`\`\`xml\n${envelope}\n\`\`\`` }] }, "chat"), false);
});

test("a user's marker question does not persist native ownership for later turns", async (t) => {
  const dataDir = await mkdtemp(join(tmpdir(), "ov-magpie-ownership-"));
  t.after(() => rm(dataDir, { recursive: true, force: true }));
  const cfg = { dataDir, baseUrl: "http://localhost:1", apiKey: "fixture", nativeAgents: [] };
  const ctx = { protocol: "chat", agent: "pi", session: "marker-question", cwd: "/fixture" };
  assert.equal(await nativeOwner(cfg, ctx, wire("chat", "Explain <openviking-context> please")), false);
  await assert.rejects(access(captureDirectory(cfg, ctx)));
  assert.equal(await nativeOwner(cfg, ctx, wire("chat", "My next ordinary question")), false);
  assert.equal(await nativeOwner(cfg, ctx, wire("chat", envelope)), true);
  assert.equal(await nativeOwner(cfg, ctx, wire("chat", "Later without a marker")), true);
});
