import assert from "node:assert/strict";
import test from "node:test";
import { guardVikingUri } from "./uri-guard.mjs";

test("uri guard blocks every DSH filesystem and shell tool that accepts paths", async () => {
  const cases = [
    ["read", { file_path: "viking://user/default/memories/profile.md" }],
    ["write", { file_path: "viking://user/default/memories/profile.md" }],
    ["edit", { file_path: "viking://user/default/memories/profile.md" }],
    ["glob", { path: "viking://user/default/memories" }],
    ["grep", { path: "viking://user/default/memories", pattern: "profile" }],
    ["bash", { command: "cat viking://user/default/memories/profile.md" }],
    ["str_replace_editor", {
      command: "view",
      path: "viking://user/default/memories/profile.md",
    }],
  ];

  for (const [name, args] of cases) {
    let delegated = false;
    const decision = await guardVikingUri({
      name,
      arguments: args,
    }, async () => {
      delegated = true;
      return { kind: "allow" };
    });

    assert.equal(delegated, false, name);
    assert.equal(decision.kind, "deny", name);
    assert.match(decision.reason, /viking:\/\/ URIs are OpenViking virtual paths/, name);
    assert.match(decision.reason, /mcp__openviking__/, name);
  }
});

test("uri guard delegates the bridged OpenViking tools and ordinary filesystem paths", async () => {
  const next = async () => ({ kind: "allow", marker: true });
  assert.deepEqual(
    await guardVikingUri({ name: "read", arguments: { file_path: "/tmp/a" } }, next),
    { kind: "allow", marker: true },
  );
  // The bridge publishes every OpenViking tool under an `mcp__openviking__`
  // name, so the guard's bare `read` / `grep` / `write` keys never shadow them.
  for (const name of ["read", "grep", "glob", "write", "edit"]) {
    assert.deepEqual(
      await guardVikingUri({
        name: `mcp__openviking__${name}`,
        arguments: { uri: "viking://user/default/memories/profile.md" },
      }, next),
      { kind: "allow", marker: true },
      name,
    );
  }
});
