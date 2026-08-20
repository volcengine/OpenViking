import assert from "node:assert/strict";
import test from "node:test";
import { registerOpenVikingTools } from "./tools.mjs";
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
  }
});

test("uri guard delegates OpenViking tools and ordinary filesystem paths", async () => {
  const next = async () => ({ kind: "allow", marker: true });
  assert.deepEqual(
    await guardVikingUri({ name: "read", arguments: { file_path: "/tmp/a" } }, next),
    { kind: "allow", marker: true },
  );
  assert.deepEqual(
    await guardVikingUri({
      name: "viking_read",
      arguments: { uri: "viking://user/default/memories/profile.md" },
    }, next),
    { kind: "allow", marker: true },
  );
});

test("archive expansion uses the archive API and renders original messages", async () => {
  const registered = [];
  const calls = [];
  registerOpenVikingTools(
    { tools: { register: definition => registered.push(definition) } },
    {
      async getSessionArchive(sessionId, archiveId, actorPeerId) {
        calls.push({ sessionId, archiveId, actorPeerId });
        return {
          archive_id: archiveId,
          abstract: "Deployment discussion",
          messages: [
            { role: "user", parts: [{ type: "text", text: "Use blue." }] },
            {
              role: "assistant",
              parts: [{
                type: "tool",
                tool_name: "bash",
                tool_input: { command: "deploy blue" },
                tool_output: "done",
              }],
            },
          ],
        };
      },
    },
    {
      async initialize() {
        return {
          ovSessionId: "dsh-session",
          config: { peerId: "workspace-peer" },
        };
      },
    },
  );
  const archive = registered.find(tool => tool.name === "viking_archive_expand");

  const rendered = await archive.execute({
    archive_id: "archive_001",
  }, { agent: {} });

  assert.deepEqual(calls, [{
    sessionId: "dsh-session",
    archiveId: "archive_001",
    actorPeerId: "workspace-peer",
  }]);
  assert.match(rendered, /## archive_001/);
  assert.match(rendered, /\*\*Messages\*\*: 2/);
  assert.match(rendered, /\[user\]: Use blue\./);
  assert.match(rendered, /\[Tool: bash\]/);
  assert.match(rendered, /deploy blue/);
  assert.match(rendered, /Output: done/);
});
