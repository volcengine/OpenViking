import assert from "node:assert/strict";
import test from "node:test";
import { createRecallCompressor } from "./compressor.mjs";

const config = { recallCompressTimeoutMs: 1000 };
function agent(stream) {
  return { options: { provider: "local", model: "strong" }, ctx: { llm: { stream } } };
}

test("host compression is a tool-free one-shot and discards reasoning", async () => {
  const run = createRecallCompressor(agent(async function* (request) {
    assert.equal(request.provider, "local");
    assert.equal(request.model, "digest-model");
    assert.equal(request.reasoningEffort, "low");
    assert.deepEqual(request.tools, []);
    assert.equal(request.sessionId, undefined);
    assert.equal(request.messages.length, 1);
    assert.equal(request.messages[0].content[0].text, "fragments");
    yield { type: "reasoning-delta", index: 0, text: "private reasoning" };
    yield { type: "text-delta", index: 1, text: "digest" };
    yield { type: "finish", reason: { kind: "stop" } };
  }), { ...config, recallCompressModel: "digest-model", recallCompressThinking: "low" });
  assert.equal(await run("fragments"), "digest");
  assert.equal(createRecallCompressor({}, config), null);
});

test("partial, truncated, and failed streams never become a digest", async () => {
  for (const kind of [null, "error", "aborted", "max-tokens", "tool-calls"]) {
    const run = createRecallCompressor(agent(async function* () {
      yield { type: "text-delta", index: 0, text: "partial digest" };
      if (kind) yield { type: "finish", reason: { kind } };
    }), config);
    await assert.rejects(run("fragments"), /Recall compression/);
  }
});

test("timeout and parent cancellation settle even if the adapter stalls", async () => {
  for (const cancel of [false, true]) {
    const parent = new AbortController();
    let requestSignal;
    let release;
    const stalled = new Promise(resolve => { release = resolve; });
    const run = createRecallCompressor(agent(async function* (request) {
      requestSignal = request.signal;
      await stalled;
    }), { ...config, recallCompressTimeoutMs: cancel ? 1000 : 10 }, parent.signal);
    const pending = run("fragments");
    if (cancel) parent.abort(new Error("User stopped"));
    await assert.rejects(pending, cancel ? /User stopped/ : /timed out/);
    assert.equal(requestSignal.aborted, true);
    release();
  }
  const parent = new AbortController();
  parent.abort(new Error("Already stopped"));
  await assert.rejects(createRecallCompressor(agent(() => assert.fail("no request")), config, parent.signal)("x"), /Already stopped/);
});

test("published DSH LLM service accepts the auxiliary request", async () => {
  const { Context } = await import("@deepseek-ai/cordis");
  const { LlmRuntime, LlmAdapter } = await import("@deepseek-ai/dsh-llm");
  const ctx = new Context();
  await ctx.plugin(LlmRuntime);
  let calls = 0;
  class Adapter extends LlmAdapter {
    async *stream(request) {
      calls += 1;
      assert.equal(request.messages[0].content[0].text, "fragments");
      yield { type: "text-delta", index: 0, text: "digest" };
      yield { type: "finish", reason: { kind: "stop" } };
    }
  }
  try {
    ctx.llm.registerAdapter(["local"], new Adapter());
    const run = createRecallCompressor({ ctx, options: { provider: "local", model: "strong" } }, config);
    assert.equal(await run("fragments"), "digest");
    assert.equal(calls, 1);
  } finally {
    await ctx.fiber.dispose();
  }
});
