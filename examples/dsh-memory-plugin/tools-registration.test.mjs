import assert from "node:assert/strict";
import test from "node:test";
import { registerOpenVikingTools } from "./tools.mjs";

// Registration-shape gate: every definition flows through the pinned
// @deepseek-ai/dsh-tools defineTool, so this test fails when a dsh rc pin
// bump changes the ToolDefinition contract these tools rely on.
test("all seven tools register as valid dsh ToolDefinitions", () => {
  const registered = [];
  const ctx = { tools: { register: definition => registered.push(definition) } };
  registerOpenVikingTools(ctx, {}, {});

  assert.deepEqual(registered.map(definition => definition.name), [
    "viking_search",
    "viking_read",
    "viking_browse",
    "viking_remember",
    "viking_forget",
    "viking_add_resource",
    "viking_archive_expand",
  ]);
  for (const definition of registered) {
    assert.equal(typeof definition.execute, "function", `${definition.name} execute`);
    assert.equal(typeof definition.output.render, "function", `${definition.name} render`);
    assert.equal(definition.output.schema.type, "string", `${definition.name} output schema`);
    assert.equal(definition.parameters.type, "object", `${definition.name} parameters`);
    const VALID_ARGS = {
      viking_search: { query: "q" },
      viking_read: { uri: "viking://x", level: "abstract" },
      viking_browse: { action: "list" },
      viking_remember: { content: "fact" },
      viking_forget: { uri: "viking://x" },
      viking_add_resource: { url: "https://y" },
      viking_archive_expand: { archive_id: "archive_001" },
    };
    const view = definition.presentCall(VALID_ARGS[definition.name]);
    assert.equal(view.card, "generic", `${definition.name} presentCall card`);
    assert.ok(["read", "other"].includes(view.kind), `${definition.name} presentCall kind`);
    assert.equal(typeof view.title, "string", `${definition.name} presentCall title`);
  }

  const [search] = registered;
  const rendered = search.output.render({}, "hit one");
  assert.deepEqual(rendered, [{ type: "text", text: "hit one" }]);
});
