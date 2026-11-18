/** The session_history default and the post-compaction/resume note, through the extension. */

import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { isolateEnv, loadExtension } from "./fixtures/extension-env.mjs";

const TOOL = "the OpenViking tool openviking_session_history";

async function startServer(toolNames) {
  const calls = [];
  const server = createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const path = (req.url || "").split("?")[0];
    if (path !== "/mcp") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ status: "ok", result: {} }));
      return;
    }
    if (req.method !== "POST") { res.writeHead(405).end(); return; }
    const message = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (message.id === undefined) { res.writeHead(202).end(); return; }
    let result;
    if (message.method === "initialize") {
      result = { protocolVersion: message.params.protocolVersion, capabilities: { tools: {} }, serverInfo: { name: "fake", version: "1" } };
    } else if (message.method === "tools/list") {
      result = { tools: toolNames.map((name) => ({ name, description: name, inputSchema: { type: "object" } })) };
    } else {
      calls.push(message.params);
      result = { content: [{ type: "text", text: "ok" }] };
    }
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ jsonrpc: "2.0", id: message.id, result }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return {
    url: `http://127.0.0.1:${server.address().port}`,
    calls,
    close: () => {
      server.closeAllConnections();
      return new Promise((resolve) => server.close(resolve));
    },
  };
}

function fakePi() {
  const handlers = new Map();
  const tools = new Map();
  return {
    pi: {
      on(event, handler) { handlers.set(event, handler); },
      registerTool(tool) { tools.set(tool.name, tool); },
      registerCommand() {},
      appendEntry() {},
      getAllTools() { return []; },
      sendMessage() {},
    },
    handlers,
    tools,
  };
}

function fakeCtx(branch) {
  return {
    sessionManager: {
      getSessionId: () => "history-session",
      getBranch: () => branch,
      getLeafId: () => branch.at(-1)?.id ?? null,
    },
    ui: { notify() {}, setStatus() {} },
    getContextUsage: () => ({ tokens: 0, contextWindow: 262144, percent: 0 }),
  };
}

const EARLIER = [
  { type: "model_change", id: "e0" },
  { type: "message", id: "e1", message: { role: "user", content: "earlier", timestamp: 1 } },
];
const checkpoint = (sessionId) => ({ type: "custom", id: "c1", customType: "ov-capture", data: { sessionId, lastEntryId: "e1" } });
const RESUMED = [...EARLIER, checkpoint("pi-history-session")];

async function withExtension(t, { plugin = {}, env = {}, branch = [], toolNames = ["find", "session_history"], reason } = {}) {
  const server = await startServer(toolNames);
  const dir = await mkdtemp(join(tmpdir(), "ov-pi-history-"));
  const restoreEnv = isolateEnv({
    OPENVIKING_CREDENTIAL_SOURCE: "env",
    OPENVIKING_URL: server.url,
    OPENVIKING_API_KEY: "pi-history-key",
    OPENVIKING_CLI_CONFIG_FILE: join(dir, "ovcli.conf"),
    OPENVIKING_CONFIG_FILE: join(dir, "ov.conf"),
    OPENVIKING_PENDING_DIR: join(dir, "pending"),
    ...env,
  });
  await writeFile(join(dir, "ovcli.conf"), JSON.stringify({ plugin: { pi: plugin } }), "utf8");

  const extension = await loadExtension();
  const { pi, handlers, tools } = fakePi();
  await extension(pi);
  const ctx = fakeCtx(branch);
  if (reason) await handlers.get("session_start")({ type: "session_start", reason }, ctx);
  t.after(async () => {
    await handlers.get("session_shutdown")({ type: "session_shutdown", reason: "quit" }, ctx);
    restoreEnv();
    await server.close();
    await rm(dir, { recursive: true, force: true });
  });
  const turn = async (prompt) => (await handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt, systemPrompt: "BASE" },
    ctx,
  ))?.systemPrompt ?? "BASE";
  const compact = () => handlers.get("session_compact")({ type: "session_compact", reason: "manual" }, ctx);
  const request = async (messages) => (await handlers.get("context")({ type: "context", messages }, ctx)).messages;
  return { server, tools, turn, compact, request };
}

test("a new session gets no note until pi compacts, then keeps it", async (t) => {
  const { turn, compact } = await withExtension(t);
  assert.doesNotMatch(await turn("hello"), /session-history/);
  await compact();
  const after = await turn("again");
  assert.match(after, /<openviking-context source="session-history">/);
  assert.match(after, /full record of this session as pi-/);
  assert.match(after, /what the compaction summary replaced/);
  assert.ok(after.includes(`look it up with ${TOOL} instead`));
  assert.match(after, /session defaults to this session/);
  assert.equal(await turn("third"), after, "the system prompt stays byte-stable after the note");
});

test("a resumed session gets the resume note from its first turn", async (t) => {
  const { turn } = await withExtension(t, { branch: RESUMED });
  const prompt = await turn("hello");
  assert.match(prompt, /anything an earlier compaction summarized/);
  assert.match(prompt, /your context no longer holds/);
});

test("a compaction inside a run puts the note after the summary until the next prompt", async (t) => {
  const { turn, compact, request } = await withExtension(t);
  const prompt = { role: "user", content: [{ type: "text", text: "go" }], timestamp: 1 };
  const before = await turn("go");
  assert.equal((await request([prompt])).length, 1);
  await compact();
  const summary = { role: "compactionSummary", summary: "s", tokensBefore: 1, timestamp: 2 };
  const kept = { role: "assistant", content: [], timestamp: 3 };
  const first = await request([summary, kept]);
  assert.deepEqual(first.map((m) => m.role), ["compactionSummary", "user", "assistant"]);
  assert.match(first[1].content[0].text, /what the compaction summary replaced/);
  const second = await request([summary, kept, { role: "toolResult", content: [], timestamp: 4 }]);
  assert.equal(second[1].content[0].text, first[1].content[0].text);
  assert.notEqual(await turn("next"), before);
  assert.deepEqual((await request([summary, kept])).map((m) => m.role), ["compactionSummary", "assistant"]);
});

test("no resume note for a branch this OV session has not captured", async (t) => {
  for (const branch of [EARLIER, [...EARLIER, checkpoint("pi-parent-session")]]) {
    const { turn } = await withExtension(t, { branch });
    assert.doesNotMatch(await turn("hello"), /session-history/);
  }
});

test("/reload adds the note only when the branch holds a compaction", async (t) => {
  const reloaded = await withExtension(t, { branch: RESUMED, reason: "reload" });
  assert.doesNotMatch(await reloaded.turn("hello"), /session-history/);
  const compacted = await withExtension(t, {
    branch: [...RESUMED, { type: "compaction", id: "k1", summary: "s", firstKeptEntryId: "e1", tokensBefore: 1 }],
    reason: "reload",
  });
  assert.match(await compacted.turn("hello"), /what the compaction summary replaced/);
});

test("the tool call is filled with this session's OV id", async (t) => {
  const { server, tools, turn } = await withExtension(t);
  await turn("hello");
  await tools.get("openviking_session_history").execute("c1", { action: "overview" });
  assert.equal(server.calls.length, 1);
  assert.equal(server.calls[0].name, "session_history");
  assert.match(server.calls[0].arguments.session, /^pi-/);
});

for (const [label, options] of [
  ["sessionHistoryPointer: false", { plugin: { sessionHistoryPointer: false } }],
  ["OPENVIKING_SESSION_HISTORY_POINTER=0", { env: { OPENVIKING_SESSION_HISTORY_POINTER: "0" } }],
  ["autoCapture: false", { plugin: { autoCapture: false } }],
  ["a server without the tool", { toolNames: ["find"] }],
]) {
  test("no note with " + label, async (t) => {
    const { turn, compact } = await withExtension(t, { branch: RESUMED, ...options });
    assert.doesNotMatch(await turn("hello"), /session-history/);
    await compact();
    assert.doesNotMatch(await turn("again"), /session-history/);
  });
}
