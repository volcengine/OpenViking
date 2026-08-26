import assert from "node:assert/strict";
import test from "node:test";
import { guardVikingUri } from "./uri-guard.mjs";

const VIKING_FILE = "viking://user/default/memories/profile.md";

async function evaluate(name, args, nextResult = { kind: "allow", marker: true }) {
  let delegated = 0;
  const exec = { name, arguments: args };
  const argumentsSnapshot = structuredClone(args);
  const decision = await guardVikingUri(exec, async () => {
    delegated += 1;
    return nextResult;
  });
  assert.deepEqual(exec.arguments, argumentsSnapshot, `${name} arguments`);
  return { decision, delegated, exec };
}

test("uri guard blocks Viking URIs in DSH filesystem path fields", async () => {
  const pathKeys = ["filePath", "file_path", "filepath", "path"];
  const toolHints = {
    read: "mcp__openviking__read",
    write: "mcp__openviking__write",
    edit: "mcp__openviking__edit",
    glob: "mcp__openviking__list",
    grep: "mcp__openviking__grep",
    str_replace_editor: "OpenViking MCP tools",
  };
  const cases = Object.entries(toolHints).flatMap(([name, hint]) =>
    pathKeys.map(key => [name, { [key]: VIKING_FILE }, hint]),
  );
  cases.push(["glob", { pattern: VIKING_FILE }, toolHints.glob]);

  for (const [name, args, hint] of cases) {
    const { decision, delegated } = await evaluate(name, args);
    const label = `${name}.${Object.keys(args)[0]}`;
    assert.equal(delegated, 0, label);
    assert.equal(decision.kind, "deny", label);
    assert.match(decision.reason, /viking:\/\/ URIs are OpenViking virtual paths/, label);
    assert.match(decision.reason, new RegExp(hint), label);
  }
});

test("uri guard allows Viking URIs in write and edit content fields", async () => {
  const cases = [
    ["write", { file_path: "/tmp/notes.md", content: `Document ${VIKING_FILE}` }],
    ["edit", { file_path: "/tmp/notes.md", old_string: VIKING_FILE, new_string: "local" }],
    ["edit", { file_path: "/tmp/notes.md", old_string: "local", new_string: VIKING_FILE }],
    ["str_replace_editor", {
      command: "str_replace",
      path: "/tmp/notes.md",
      old_str: "local",
      new_str: VIKING_FILE,
    }],
  ];

  for (const [name, args] of cases) {
    const { decision, delegated } = await evaluate(name, args);
    assert.equal(delegated, 1, name);
    assert.deepEqual(decision, { kind: "allow", marker: true }, name);
  }
});

test("uri guard allows grep queries and metadata to mention Viking URIs", async () => {
  const cases = [
    ["grep", { path: "/tmp", pattern: VIKING_FILE }],
    ["write", {
      file_path: "/tmp/notes.md",
      content: "ordinary",
      metadata: { source: VIKING_FILE },
    }],
    ["read", { file_path: "/tmp/notes.md", description: VIKING_FILE }],
  ];

  for (const [name, args] of cases) {
    const { decision, delegated } = await evaluate(name, args);
    assert.equal(delegated, 1, name);
    assert.deepEqual(decision, { kind: "allow", marker: true }, name);
  }
});

test("uri guard keeps the conservative shell command check", async () => {
  const { decision, delegated } = await evaluate("bash", {
    command: `cat ${VIKING_FILE}`,
    description: "local command",
  });

  assert.equal(delegated, 0);
  assert.equal(decision.kind, "deny");
  assert.match(decision.reason, /mcp__openviking__/);
});

test("uri guard ignores non-command bash metadata", async () => {
  const { decision, delegated } = await evaluate("bash", {
    command: "pwd",
    description: VIKING_FILE,
  });

  assert.equal(delegated, 1);
  assert.deepEqual(decision, { kind: "allow", marker: true });
});

test("uri guard handles empty arguments and nested selected path values", async () => {
  for (const args of [null, {}, undefined]) {
    const { decision, delegated } = await evaluate("write", args);
    assert.equal(delegated, 1);
    assert.deepEqual(decision, { kind: "allow", marker: true });
  }

  const { decision, delegated } = await evaluate("read", {
    path: ["/tmp/a", { nested: VIKING_FILE }],
  });
  assert.equal(delegated, 0);
  assert.equal(decision.kind, "deny");
});

test("uri guard reports the selected path URI without leaking content URIs", async () => {
  const pathUri = "viking://resources/path-target.md";
  const contentUri = "viking://user/private/content-only.md";
  const { decision, delegated } = await evaluate("write", {
    file_path: pathUri,
    content: contentUri,
  });

  assert.equal(delegated, 0);
  assert.equal(decision.kind, "deny");
  assert.match(decision.reason, /viking:\/\/resources\/path-target\.md/);
  assert.doesNotMatch(decision.reason, /content-only/);
});

test("uri guard delegates bridged OpenViking and non-guarded tools", async () => {
  for (const name of [
    "mcp__openviking__read",
    "mcp__openviking__grep",
    "mcp__openviking__glob",
    "mcp__openviking__write",
    "mcp__openviking__edit",
    "custom_tool",
  ]) {
    const { decision, delegated } = await evaluate(name, {
      uri: VIKING_FILE,
      content: VIKING_FILE,
    });
    assert.equal(delegated, 1, name);
    assert.deepEqual(decision, { kind: "allow", marker: true }, name);
  }
});
