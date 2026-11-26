import assert from "node:assert/strict";
import test from "node:test";
import { apply } from "./index.mjs";

test("session filtering skips subagents without changing main-session recall", async () => {
  const handlers = new Map();
  let memoryRuntime;
  const ctx = {
    logger: { debug() {} },
    provide(name, value) {
      if (name === "openvikingMemory") memoryRuntime = value;
    },
    effect(execute) {
      execute();
      return async () => {};
    },
    tools: { register() {} },
    plugin() {},
    on(name, handler) {
      handlers.set(name, handler);
    },
  };
  apply(ctx, {
    endpoint: "http://127.0.0.1:1933",
    workspacePeer: false,
    skipSubagentSessions: true,
  });

  const agent = {
    session: { id: "dsh-final-batch", header: { cwd: "/workspace" } },
    ctx: {
      effect(execute) {
        execute();
        return async () => {};
      },
    },
  };
  const created = handlers.get("agent/created");
  assert.equal(typeof created, "function");

  const preStep = handlers.get("agent/pre-step");
  const initial = [message("initial input")];
  const downstream = [message("downstream replacement")];
  const seen = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    const path = new URL(url).pathname;
    if (path === "/health") return response({});
    if (path === "/api/v1/sessions") return response({});
    if (path === "/api/v1/search/search") {
      seen.push(JSON.parse(init.body).query);
      return response({ rendered: "" });
    }
    if (path === "/api/v1/fs/ls") return response([]);
    return response({}, 404);
  };

  try {
    await preStep({
      agent,
      messages: initial,
      signal: new AbortController().signal,
    }, async () => ({ kind: "enter", messages: downstream }));

    const child = {
      status: "idle",
      session: {
        id: "dsh-derived-child",
        header: { cwd: "/workspace", origin: "subagent" },
      },
      inject() {
        assert.fail("subagent profile must not be injected");
      },
      ctx: {
        effect() {
          assert.fail("subagent teardown commit must not be registered");
        },
      },
    };
    assert.equal(await created({ agent: child, source: "startup" }), undefined);
    assert.equal(await handlers.get("agent/session-start")({ agent: child }), false);
    const childMessages = [message("derived worker input")];
    const childDecision = await preStep({
      agent: child,
      messages: childMessages,
      signal: new AbortController().signal,
    }, async () => ({ kind: "enter", messages: childMessages }));
    handlers.get("session/event")(child.session, {
      type: "user/message",
      data: message("derived exploration chatter"),
    });
    handlers.get("session/event")(child.session, { type: "turn/end", data: {} });
    await handlers.get("session/flush")(child.session);

    assert.deepEqual(childDecision.messages, childMessages);
    assert.equal(memoryRuntime.states.has(child.session.id), false);
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.deepEqual(seen, ["downstream replacement"]);
});

function mountPlugin(tools = { register() {} }, sessions) {
  const handlers = new Map();
  let runtime;
  const ctx = {
    logger: { debug() {} },
    provide(name, value) {
      if (name === "openvikingMemory") runtime = value;
    },
    effect(execute) {
      execute();
      return async () => {};
    },
    tools,
    sessions,
    plugin() {},
    on(name, handler) {
      handlers.set(name, handler);
    },
  };
  apply(ctx, { endpoint: "http://127.0.0.1:1933", workspacePeer: false });
  runtime.stopDrainer();
  // Replace only the network-bound part; initialize/profileMessage stay real so
  // the single-flight and delivered-once guarantees are the plugin's own.
  const calls = { initialize: 0 };
  runtime.initializeState = async state => {
    calls.initialize += 1;
    state.profileBlock = "<openviking-context source=\"profile\">p</openviking-context>";
    state.ready = true;
    return state;
  };
  return { handlers, runtime, calls };
}

function startupAgent(id = "dsh-startup") {
  const agent = {
    status: "idle",
    session: { id, header: { cwd: "/workspace" } },
    inbox: { nextTurn: [], nextStep: [] },
    injected: [],
    effects: [],
    inject(message) {
      this.injected.push(message);
      this.inbox.nextStep.push(message);
    },
    ctx: {
      effect(execute, label) {
        agent.effects.push(label);
        return async () => {};
      },
    },
  };
  return agent;
}

async function withQuietFetch(run) {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => response({ rendered: "" });
  try {
    return await run();
  } finally {
    globalThis.fetch = originalFetch;
  }
}

test("agent/created (dsh 0.1.7+) runs startup once and pre-step does not re-inject the profile", async () => {
  const { handlers, calls } = mountPlugin();
  const agent = startupAgent();
  await withQuietFetch(async () => {
    const result = await handlers.get("agent/created")({
      agent,
      source: "startup",
      signal: new AbortController().signal,
    });
    // Anything but undefined would bail DSH's serial agent/created dispatch.
    assert.equal(result, undefined);
    assert.equal(calls.initialize, 1);
    assert.equal(agent.injected.length, 1);
    assert.match(agent.injected[0].content[0].text, /source="profile"/);
    assert.deepEqual(agent.effects, ["openvikingMemory.disposeSession()"]);

    // A stray session-start on the same agent must not repeat startup.
    await handlers.get("agent/session-start")({ agent, source: "startup" });
    const input = [message("first prompt")];
    const decision = await handlers.get("agent/pre-step")({
      agent,
      messages: input,
      signal: new AbortController().signal,
    }, async () => ({ kind: "enter", messages: input }));

    assert.equal(calls.initialize, 1);
    assert.equal(agent.injected.length, 1);
    assert.deepEqual(agent.effects, ["openvikingMemory.disposeSession()"]);
    assert.equal(
      decision.messages.some(m => /source="profile"/.test(m.content?.[0]?.text || "")),
      false,
    );
  });
});

test("agent/pre-step keeps the downstream decision's other fields when it appends", async () => {
  const { handlers } = mountPlugin();
  const agent = startupAgent("dsh-goal-round");
  await withQuietFetch(async () => {
    const input = [message("goal round prompt")];
    // goal-round-driver marks the entry that opens a request series.
    const decision = await handlers.get("agent/pre-step")({
      agent,
      messages: input,
      signal: new AbortController().signal,
    }, async () => ({ kind: "enter", messages: input, startsRequestSeries: true }));

    assert.equal(decision.kind, "enter");
    assert.equal(decision.startsRequestSeries, true);
    assert.equal(decision.messages[0], input[0]);
    assert.ok(decision.messages.length > input.length, "the profile block was appended");
  });
});

test("dsh 0.1.x: source-less agent/created defers startup to agent/session-start", async () => {
  const { handlers, calls } = mountPlugin();
  const agent = startupAgent("dsh-legacy");
  await withQuietFetch(async () => {
    assert.equal(await handlers.get("agent/created")({ agent }), undefined);
    assert.equal(calls.initialize, 0);
    assert.deepEqual(agent.effects, []);

    assert.equal(
      await handlers.get("agent/session-start")({ agent, source: "startup" }),
      true,
    );
    assert.equal(calls.initialize, 1);
    assert.equal(agent.injected.length, 1);
    assert.deepEqual(agent.effects, ["openvikingMemory.disposeSession()"]);
  });
});

test("agent/created returns on abort and never rejects on startup failure", async () => {
  const { handlers, runtime } = mountPlugin();
  let release;
  runtime.initializeState = () => new Promise(resolve => {
    release = resolve;
  });
  const controller = new AbortController();
  const agent = startupAgent("dsh-aborted");
  const pending = handlers.get("agent/created")({
    agent,
    source: "startup",
    signal: controller.signal,
  });
  controller.abort();
  assert.equal(await pending, undefined);
  release({ ready: false });
  assert.deepEqual(agent.injected, []);

  runtime.initializeState = async () => {
    throw new Error("boom");
  };
  const failing = startupAgent("dsh-failing");
  assert.equal(
    await handlers.get("agent/created")({ agent: failing, source: "startup" }),
    undefined,
  );
  assert.deepEqual(failing.injected, []);
});

test("the session-history note comes on the next step after a compaction or a resume", async () => {
  const { handlers } = mountPlugin();
  const stepNotes = async agent => {
    const input = [message("next prompt")];
    const decision = await handlers.get("agent/pre-step")({
      agent,
      messages: input,
      signal: new AbortController().signal,
    }, async () => ({ kind: "enter", messages: input }));
    return decision.messages.map(m => m.content?.[0]?.text || "").filter(t => t.includes('source="session-history"'));
  };
  const compacted = startupAgent("compacted");
  const resumed = startupAgent("resumed");
  await withQuietFetch(async () => {
    assert.deepEqual(await stepNotes(compacted), []);
    handlers.get("session/event")(compacted.session, { type: "compaction/end", data: { turn: 1 } });
    const notes = await stepNotes(compacted);
    assert.equal(notes.length, 1);
    assert.match(notes[0], /as dsh-compacted, including what the compaction summary replaced/);
    assert.deepEqual(await stepNotes(compacted), []);

    await handlers.get("agent/created")({ agent: resumed, source: "resume" });
    assert.match((await stepNotes(resumed))[0], /as dsh-resumed, including anything an earlier compaction summarized/);
  });
});

test("a session_history call without a session gets the caller's id and its parent chain", async () => {
  const calls = [];
  const tools = { register() {}, get: () => ({ execute: async args => (calls.push(args), "hit") }) };
  const sessions = new Map([
    ["fork", { header: { parentSession: "main", isSeeded: true } }],
    ["main", { header: { isSeeded: false } }],
  ]);
  const { handlers } = mountPlugin(tools, sessions);
  const agent = { session: { id: "sub", header: { parentSession: "fork", isSeeded: true, origin: "subagent" } } };
  const call = (args, next) => handlers.get("tools/execute")({ name: "mcp__openviking__session_history", arguments: args, agent }, next);
  const result = await call(Object.freeze({ action: "overview" }), async () => assert.fail("a call without session must be filled"));
  assert.deepEqual(calls, [{ action: "overview", session: "dsh-sub,dsh-fork,dsh-main" }]);
  assert.equal(result.value, "hit");
  assert.equal(await call({ session: "dsh-other" }, async () => "untouched"), "untouched");
  assert.equal(calls.length, 1);
});

function message(text) {
  return {
    role: "user",
    content: [{ type: "text", text }],
    source: { kind: "user" },
  };
}

function response(result, status = 200) {
  return new Response(JSON.stringify({
    status: status < 400 ? "ok" : "error",
    ...(status < 400 ? { result } : { error: { code: "NOT_FOUND" } }),
  }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("mcpEnabled false mounts neither the MCP tools nor the skills", () => {
  const mounted = (input) => {
    let count = 0;
    apply({
      logger: { debug() {} },
      provide() {},
      effect: (execute) => { execute(); return async () => {}; },
      tools: { register() {} },
      plugin() { count += 1; },
      on() {},
    }, { endpoint: "http://127.0.0.1:1933", workspacePeer: false, ...input });
    return count;
  };
  assert.equal(mounted({}), 2);
  assert.equal(mounted({ mcpEnabled: false }), 0);
});
