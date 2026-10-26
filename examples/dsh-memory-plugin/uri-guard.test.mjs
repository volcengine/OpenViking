import assert from "node:assert/strict";
import test from "node:test";
import { OPENVIKING_PLUGIN_KIND } from "./capture.mjs";
import { guardVikingUri, noticeVikingUri } from "./uri-guard.mjs";

test("uri guard blocks every DSH filesystem tool that accepts paths", async () => {
  const cases = [
    ["read", { file_path: "viking://user/default/memories/profile.md" }],
    ["write", { file_path: "viking://user/default/memories/profile.md" }],
    ["edit", { file_path: "viking://user/default/memories/profile.md" }],
    ["glob", { path: "viking://user/default/memories" }],
    ["grep", { path: "viking://user/default/memories", pattern: "profile" }],
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
  ];

  for (const [name, args] of cases) {
    const decision = await guardVikingUri({ name, arguments: args }, next);
    assert.equal(decision.kind, "deny", name);
  }
});

test("uri guard lets grep search for viking URI text in a local tree", async () => {
  const exec = { name: "grep", arguments: { pattern: "viking://user/default", path: "/tmp" } };
  const next = async () => ({ kind: "allow", marker: true });
  assert.deepEqual(await guardVikingUri(exec, next), { kind: "allow", marker: true });
  const accepted = { kind: "accept" };
  assert.equal(await noticeVikingUri(exec, okResult(), async () => accepted), accepted);
});

test("shell commands carrying a viking URI run and get a notice", async () => {
  const exec = {
    name: "bash",
    arguments: { command: "ov read viking://user/default/memories/profile.md" },
  };
  let delegated = false;
  await guardVikingUri(exec, async () => {
    delegated = true;
    return { kind: "allow" };
  });
  assert.equal(delegated, true);

  const decision = await noticeVikingUri(exec, okResult(), async () => ({ kind: "accept" }));
  assert.equal(decision.kind, "accept");
  assert.equal(decision.additionalContexts.length, 1);
  const [context] = decision.additionalContexts;
  assert.equal(context.role, "user");
  assert.equal(context.source.kind, OPENVIKING_PLUGIN_KIND);
  assert.equal(context.source.plugin, "openviking-memory");
  assert.equal(context.source.form, "notice");
  assert.match(context.source.summary, /viking:\/\/user\/default\/memories\/profile\.md/);
  assert.match(context.content[0].text, /ignore this notice/);
  assert.match(context.content[0].text, /mcp__openviking__read/);
});

test("the notice summary stays within dsh's context summary bound", async () => {
  const uri = `viking://resources/${"deep/".repeat(60)}file.md`;
  const decision = await noticeVikingUri(
    { name: "bash", arguments: { command: `cat ${uri}` } },
    okResult(),
    async () => ({ kind: "accept" }),
  );
  const { summary } = decision.additionalContexts[0].source;
  assert.ok(summary.length > 0);
  assert.ok(summary.length <= 120, `summary is ${summary.length} chars`);
});

test("the notice keeps a downstream decision and its contexts", async () => {
  const exec = { name: "bash", arguments: { command: "cat viking://user/default/memories/p.md" } };
  const earlier = { id: "earlier-context" };
  const feedback = [{ type: "text", text: "blocked downstream" }];
  const decision = await noticeVikingUri(exec, okResult(), async () => ({
    kind: "block",
    feedback,
    additionalContexts: [earlier],
  }));
  assert.equal(decision.kind, "block");
  assert.equal(decision.feedback, feedback);
  assert.equal(decision.additionalContexts.length, 2);
  assert.equal(decision.additionalContexts[0], earlier);
  assert.equal(decision.additionalContexts[1].source.form, "notice");
});

test("the notice leaves file tools, plain shell commands, and denied reads alone", async () => {
  const cases = [
    [{ name: "read", arguments: { file_path: "viking://user/default/memories/p.md" } }, {
      isError: true,
      error: { message: "viking:// URIs are OpenViking virtual paths" },
      content: [{ type: "text", text: "Error: viking:// URIs are OpenViking virtual paths" }],
    }],
    [{ name: "write", arguments: { file_path: "/tmp/a.md", content: "viking://user/default/" } }, okResult()],
    [{ name: "bash", arguments: { command: "ls /tmp" } }, okResult()],
    [{ name: "mcp__openviking__read", arguments: { uris: "viking://user/default/" } }, okResult()],
  ];

  for (const [exec, result] of cases) {
    const downstream = { kind: "accept" };
    assert.equal(await noticeVikingUri(exec, result, async () => downstream), downstream, exec.name);
  }
});

function okResult() {
  return { isError: false, value: "", content: [{ type: "text", text: "" }] };
}

test("a write or edit aimed at a skill names the bridged add_skill tool", async () => {
  for (const name of ["write", "edit"]) {
    const decision = await guardVikingUri({
      name,
      arguments: { file_path: "viking://agent/skills/deploy-runbook/SKILL.md" },
    }, async () => ({ kind: "allow" }));
    assert.equal(decision.kind, "deny", name);
    assert.match(decision.reason, /Use mcp__openviking__add_skill instead\./, name);
  }
});
