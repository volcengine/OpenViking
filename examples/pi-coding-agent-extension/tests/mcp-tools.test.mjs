import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerMcpTools } from "../tools.ts";

const { tools } = JSON.parse(readFileSync(new URL("fixtures/mcp-tools-list.json", import.meta.url)));
function register(descriptors = tools) {
  const definitions = new Map();
  const calls = [];
  const names = registerMcpTools({ registerTool: (tool) => definitions.set(tool.name, tool) }, {
    state: { tools: descriptors },
    callTool: async (...args) => { calls.push(args); return { content: [] }; },
  });
  return { definitions, names, calls };
}

test("registers the server catalogue with its schemas and descriptions", () => {
  const { definitions, names } = register();
  assert.deepEqual(names, tools.map((tool) => "openviking_" + tool.name));
  for (const tool of tools) {
    const definition = definitions.get("openviking_" + tool.name);
    assert.deepEqual(definition.parameters, tool.inputSchema);
    assert.equal(definition.description, tool.description.trim());
    assert.equal(definition.promptSnippet, undefined);
    assert.equal(definition.promptGuidelines, undefined);
  }
  const added = register([{ name: "future_tool", inputSchema: { type: "object" } }]);
  assert.deepEqual(added.names, ["openviking_future_tool"]);
});

test("validates original arguments before pi can coerce them", () => {
  const { definitions } = register();
  for (const [tool, args] of [
    ["write", { uri: "viking://user/u/memories/a.md", content: "hello", mode: null }],
    ["remember", { messages: { role: "user", content: "hello" } }],
    ["remember", { messages: '[{"role":"user","content":"hello"}]' }],
    ["find", { query: "hello", level: "1" }],
    ["read", { uris: "viking://user/u/memories/a.md" }],
  ]) {
    const before = structuredClone(args);
    assert.throws(() => definitions.get("openviking_" + tool).prepareArguments(args), /Invalid arguments/);
    assert.deepEqual(args, before);
  }
});

test("preserves valid arguments and forwards the bare tool name and cancellation", async () => {
  const { definitions, calls } = register();
  const tool = definitions.get("openviking_read");
  const args = { uris: ["viking://user/u/memories/a.md"], offset: 0, limit: 20 };
  const before = structuredClone(args);
  assert.equal(tool.prepareArguments(args), args);
  assert.deepEqual(args, before);
  const { signal } = new AbortController();
  await tool.execute("call-1", args, signal);
  assert.deepEqual(calls, [["read", args, { signal }]]);
});

test("enforces additionalProperties and required fields without adding defaults", () => {
  const { definitions } = register([{ name: "example", inputSchema: {
    type: "object", required: ["value"], additionalProperties: false,
    properties: { value: { type: "integer" }, mode: { type: "string", default: "replace" } },
  } }]);
  const tool = definitions.get("openviking_example");
  assert.throws(() => tool.prepareArguments({}), /Invalid arguments/);
  assert.throws(() => tool.prepareArguments({ value: 1, extra: 2 }), /Invalid arguments/);
  const args = { value: 1 };
  tool.prepareArguments(args);
  assert.deepEqual(args, { value: 1 });
});
