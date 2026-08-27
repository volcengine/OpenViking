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

// #4188 — the guard scanned every argument value, so a local write or edit whose
// CONTENT merely mentioned a viking URI was denied and no file was created.
// Measured before the fix, with an ordinary local file_path:
//
//   write { content: "docs say viking://user/default/ is virtual" } -> deny
//   edit  { new_string: "see viking://user/default/" }              -> deny
test("uri guard ignores a viking URI that appears in file content", async () => {
  const next = async () => ({ kind: "allow", marker: true });
  const cases = [
    ["write", { file_path: "/home/me/notes.md", content: "docs say viking://user/default/ is virtual" }],
    ["edit", {
      file_path: "/home/me/notes.md",
      old_string: "old",
      new_string: "see viking://user/default/memories/",
    }],
    ["str_replace_editor", {
      command: "create",
      path: "/tmp/notes.md",
      file_text: "viking://user/default/",
    }],
  ];

  for (const [name, args] of cases) {
    assert.deepEqual(
      await guardVikingUri({ name, arguments: args }, next),
      { kind: "allow", marker: true },
      name,
    );
  }
});

test("uri guard still denies a viking URI used as a location", async () => {
  const next = async () => ({ kind: "allow" });
  const cases = [
    // Same tools as above, with the URI where a path belongs — content is
    // skipped by key name, so the path argument still decides.
    ["write", { file_path: "viking://user/default/memories/p.md", content: "harmless text" }],
    ["edit", {
      file_path: "viking://user/default/memories/p.md",
      old_string: "a",
      new_string: "b",
    }],
    // A path key the list does not know about is still swept, so the fallback
    // keeps its reason to exist.
    ["glob", { targets: { primary: "viking://user/default/memories" } }],
    // bash carries its path inside `command`, which is not content.
    ["bash", { command: "cat viking://user/default/memories/p.md" }],
  ];

  for (const [name, args] of cases) {
    const decision = await guardVikingUri({ name, arguments: args }, next);
    assert.equal(decision.kind, "deny", name);
  }
});
