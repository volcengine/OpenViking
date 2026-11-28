import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { apply } from "./index.mjs";

const INPUT = { endpoint: "http://127.0.0.1:1933", workspacePeer: false };
const message = text => ({ role: "user", content: [{ type: "text", text }], source: { kind: "user" } });
const effectCtx = () => {
  const disposers = [];
  return { disposers, effect(execute) { disposers.push(execute()); return async () => {}; } };
};

function setup(t, input = {}) {
  const root = mkdtempSync(join(tmpdir(), "dsh-plugin-"));
  const saved = { ...process.env };
  for (const key of Object.keys(process.env)) if (key.startsWith("OPENVIKING_")) delete process.env[key];
  Object.assign(process.env, {
    OPENVIKING_CONFIG_FILE: join(root, "ov.conf"),
    OPENVIKING_CLI_CONFIG_FILE: join(root, "ovcli.conf"),
    OPENVIKING_HOME: join(root, "home"),
    OPENVIKING_STATE_DIR: join(root, "state"),
    OPENVIKING_HOOK_STATE_DIR: join(root, "hook-state"),
    OPENVIKING_PENDING_DIR: join(root, "pending"),
  });
  const requests = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const { pathname } = new URL(url);
    requests.push({ method: init.method || "GET", path: pathname, body: init.body ? JSON.parse(init.body) : undefined });
    const result = pathname === "/api/v1/content/read" ? "User likes blue." : pathname.startsWith("/api/v1/fs/ls") ? [] : {};
    return new Response(JSON.stringify({ status: "ok", result }), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
    process.env = saved;
    rmSync(root, { recursive: true, force: true });
  });
  const handlers = new Map();
  const warnings = [];
  const plugin = effectCtx();
  apply({
    logger: { debug() {}, warn: line => warnings.push(line) },
    ...plugin,
    tools: { register() {} },
    plugin() {},
    on: (name, handler) => handlers.set(name, handler),
  }, { ...INPUT, ...input });
  return { handlers, requests, warnings, plugin };
}

function agentFor(id, header = {}) {
  const injected = [];
  return { status: "idle", injected, inject: m => injected.push(m), session: { id, header: { cwd: tmpdir(), ...header } }, ctx: effectCtx() };
}

test("a session runs through the flow: profile at startup, recall per prompt, ordered capture, commits", async t => {
  const { handlers, requests } = setup(t);
  const agent = agentFor("s1");
  assert.equal(await handlers.get("agent/created")({ agent, source: "startup" }), undefined);
  assert.equal(agent.injected.length, 1);
  assert.equal(agent.injected[0].source.form, "instructions");
  assert.match(agent.injected[0].content[0].text, /User likes blue\./);
  assert.ok(!requests.some(r => r.path === "/health" || (r.method === "POST" && r.path === "/api/v1/sessions")));

  const batch = [message("what colour do we deploy with?")];
  const decision = await handlers.get("agent/pre-step")({ agent, signal: new AbortController().signal }, async () => ({ kind: "enter", messages: batch, startsRequestSeries: true }));
  assert.equal(decision.startsRequestSeries, true);
  const search = requests.find(r => r.path.startsWith("/api/v1/search/"));
  assert.equal(search.body.query, "what colour do we deploy with?");

  const onEvent = handlers.get("session/event");
  onEvent(agent.session, { type: "user/message", data: message("first") });
  onEvent(agent.session, { type: "assistant/message", data: { message: { role: "assistant", content: [{ type: "text", text: "second" }] } } });
  onEvent(agent.session, { type: "turn/end", data: {} });
  onEvent(agent.session, { type: "compaction/start", data: {} });
  await handlers.get("session/flush")(agent.session);
  const writes = requests.filter(r => r.method === "POST" && r.path.startsWith("/api/v1/sessions/dsh-s1/"));
  const texts = writes.filter(r => r.path.endsWith("/messages/batch")).flatMap(r => r.body.messages.map(m => m.parts[0].text));
  assert.deepEqual(texts, ["first", "second"]);
  assert.equal(writes.filter(r => r.path.endsWith("/commit")).length, 1);

  onEvent(agent.session, { type: "user/message", data: message("third") });
  await Promise.all(agent.ctx.disposers.map(dispose => dispose()));
  assert.equal(requests.filter(r => r.path === "/api/v1/sessions/dsh-s1/commit").length, 2);
});

test("an older DSH gets one unsupported line and nothing else", async t => {
  const { handlers, requests, warnings } = setup(t);
  const agent = agentFor("old");
  await handlers.get("agent/created")({ agent });
  await handlers.get("agent/created")({ agent: agentFor("old2") });
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /not supported/);
  handlers.get("session/event")(agent.session, { type: "user/message", data: message("hello") });
  const decision = await handlers.get("agent/pre-step")({ agent, signal: new AbortController().signal }, async () => ({ kind: "enter", messages: [message("hello there")] }));
  assert.equal(decision.messages.length, 1);
  await handlers.get("session/flush")(agent.session);
  assert.deepEqual(requests, []);
  assert.equal(handlers.has("agent/session-start"), false);
});

test("skipSubagentSessions leaves subagents alone", async t => {
  const { handlers, requests } = setup(t, { skipSubagentSessions: true });
  const child = agentFor("child", { origin: "subagent" });
  await handlers.get("agent/created")({ agent: child, source: "startup" });
  handlers.get("session/event")(child.session, { type: "user/message", data: message("chatter") });
  await handlers.get("session/flush")(child.session);
  assert.deepEqual(child.injected, []);
  assert.deepEqual(requests, []);
});

test("a session_history call without a session gets the caller's id and its parent chain", async () => {
  const handlers = new Map();
  const calls = [];
  apply({
    logger: {},
    effect: () => async () => {},
    tools: { register() {}, get: () => ({ execute: async args => (calls.push(args), "hit") }) },
    sessions: new Map([["fork", { header: { parentSession: "main", isSeeded: true } }], ["main", { header: {} }]]),
    plugin() {},
    on: (name, handler) => handlers.set(name, handler),
  }, INPUT);
  const agent = { session: { id: "sub", header: { parentSession: "fork", isSeeded: true } } };
  const call = (args, next) => handlers.get("tools/execute")({ name: "mcp__openviking__session_history", arguments: args, agent }, next);
  const result = await call(Object.freeze({ action: "overview" }), async () => assert.fail("a call without session must be filled"));
  assert.deepEqual(calls, [{ action: "overview", session: "dsh-sub,dsh-fork,dsh-main" }]);
  assert.equal(result.value, "hit");
  assert.equal(await call({ session: "dsh-other" }, async () => "untouched"), "untouched");
});

test("mcpEnabled false mounts neither the MCP tools nor the skills", () => {
  const mounted = input => {
    let count = 0;
    apply({ logger: {}, effect: () => async () => {}, tools: { register() {} }, plugin() { count += 1; }, on() {} }, { ...INPUT, ...input });
    return count;
  };
  assert.equal(mounted({}), 2);
  assert.equal(mounted({ mcpEnabled: false }), 0);
});
