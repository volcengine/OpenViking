import assert from "node:assert/strict";
import test from "node:test";
import { isAuxiliaryRequest } from "../conversation.mjs";
import { onRequest, onResponse } from "../hooks.mjs";
import { recallRequest } from "../runtime.mjs";
import { buildRecallCompressionPrompt } from "../shared/recall-compress-core.mjs";

for (const protocol of ["chat", "responses", "anthropic", "gemini"]) {
  test(`${protocol} skips native OV compressor calls without requiring context markers`, async () => {
    const prompt = buildRecallCompressionPrompt({ query: "my occupation and email", rendered: "historical data", maxBullets: 12 });
    const body = protocol === "gemini" ? { contents: [{ role: "user", parts: [{ text: prompt }] }] }
      : protocol === "responses" ? { store: false, input: [{ role: "user", content: [{ type: "input_text", text: prompt }] }] }
        : { messages: [{ role: "user", content: [{ type: "text", text: prompt }] }] };
    const reports = [];
    const ctx = { protocol, origin: "local", session: "native-compressor", state: {}, report: (code) => reports.push(code) };
    assert.equal(isAuxiliaryRequest(body, ctx), true);
    assert.equal(await onRequest(body, ctx), undefined);
    assert.equal(await recallRequest(body, ctx, { OPENVIKING_CLI_CONFIG_FILE: "/missing-auxiliary-config" }), undefined);
    assert.deepEqual(reports, ["skipped", "skipped"]);
    assert.deepEqual(ctx.state, {});
    await onResponse({ choices: [{ finish_reason: "stop", message: { role: "assistant", content: "digest" } }] }, { ...ctx, success: true });
  });
}

test("ordinary questions and assistant/tool quotations do not become utility calls", () => {
  const prompt = buildRecallCompressionPrompt({ query: "q", rendered: "data" });
  for (const messages of [
    [{ role: "user", content: "Explain the memory relevance compressor utility" }],
    [{ role: "user", content: `Please review this prompt:\n${prompt}` }],
    [{ role: "assistant", content: prompt }, { role: "tool", content: prompt }, { role: "user", content: "Explain the example above" }],
  ]) assert.equal(isAuxiliaryRequest({ messages }, { protocol: "chat" }), false);
});
