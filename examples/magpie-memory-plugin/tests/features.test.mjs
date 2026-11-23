import { test } from "node:test";
import assert from "node:assert/strict";
import OpenViking from "../index.mjs";
import { onRequest, onResponse } from "../hooks.mjs";

test("memory-only mode keeps tools without starting model management", async () => {
  const hooks = await OpenViking({}, { modelsEnabled: false });
  assert.ok(hooks.tool.find); assert.ok(hooks.tool.remember);
  assert.equal(globalThis[Symbol.for("openviking.magpie-models.adapter.v1")], undefined);
});
test("model-only mode starts independently and does not register memory tools", async () => {
  const key = Symbol.for("openviking.magpie-models.adapter.v1");
  try {
    const hooks = await OpenViking({}, { memoryEnabled: false });
    assert.deepEqual(hooks, {});
    assert.equal(typeof globalThis[key], "function");
  } finally { globalThis[key]?.(); delete globalThis[key]; }
});
test("both features can be disabled, and invalid flags are rejected", async () => {
  assert.deepEqual(await OpenViking({}, { memoryEnabled: false, modelsEnabled: false }), {});
  await assert.rejects(OpenViking({}, { memoryEnabled: "false" }));
  await assert.rejects(OpenViking({}, { modelsEnabled: "false" }));
});
test("disabled memory hooks skip before reading credentials or processing capture", async () => {
  const reports = [];
  const ctx = { options: { memoryEnabled: false }, state: { capture: { invalid: true } }, report: (...args) => reports.push(args) };
  assert.equal(await onRequest({ messages: [] }, ctx), undefined);
  assert.equal(await onResponse({}, ctx), undefined);
  assert.deepEqual(reports, [["skipped", 0]]);
  assert.deepEqual(ctx.state, { capture: { invalid: true } });
});
