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

function mountPlugin(input = {}, tools = { register() {} }, sessions = new Map()) {
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
  apply(ctx, { endpoint: "http://127.0.0.1:1933", workspacePeer: false, ...input });
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
    injected: [],
    effects: [],
    inject(message) {
      this.injected.push(message);
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

async function preStepTexts(handlers, agent) {
  const input = [message("next prompt")];
  const decision = await handlers.get("agent/pre-step")({
    agent,
    messages: input,
    signal: new AbortController().signal,
  }, async () => ({ kind: "enter", messages: input }));
  return decision.messages.map(m => m.content?.[0]?.text || "");
}

const historyNote = text => text.includes('source="session-history"');

test("a finished compaction adds the session-history note to the next step, once", async () => {
  const { handlers } = mountPlugin();
  const agent = startupAgent("compacted");
  await withQuietFetch(async () => {
    await preStepTexts(handlers, agent);
    handlers.get("session/event")(agent.session, {
      type: "compaction/end",
      data: { compactionId: "c1", turn: 1, error: "summarizer failed" },
    });
    assert.equal((await preStepTexts(handlers, agent)).some(historyNote), false);

    handlers.get("session/event")(agent.session, {
      type: "compaction/end",
      data: { compactionId: "c2", turn: 2 },
    });
    const notes = (await preStepTexts(handlers, agent)).filter(historyNote);
    assert.equal(notes.length, 1);
    assert.match(notes[0], /full record of this session as dsh-compacted, including what the compaction summary replaced/);
    assert.match(notes[0], /session defaults to this session/);
    assert.equal((await preStepTexts(handlers, agent)).some(historyNote), false);
  });
});

test("a fork's compaction note names its parent's record too", async () => {
  const { handlers } = mountPlugin();
  const agent = startupAgent("forked");
  agent.session.header = { cwd: "/workspace", parentSession: "origin", isSeeded: true };
  await withQuietFetch(async () => {
    handlers.get("session/event")(agent.session, { type: "compaction/end", data: { turn: 1 } });
    const [note] = (await preStepTexts(handlers, agent)).filter(historyNote);
    assert.match(note, /as dsh-forked,dsh-origin, including/);
  });
});

test("a resumed agent gets the session-history note on its first step", async () => {
  const { handlers } = mountPlugin();
  const agent = startupAgent("resumed");
  await withQuietFetch(async () => {
    await handlers.get("agent/created")({ agent, source: "resume" });
    const notes = (await preStepTexts(handlers, agent)).filter(historyNote);
    assert.equal(notes.length, 1);
    assert.match(notes[0], /as dsh-resumed, including anything an earlier compaction summarized/);

    const fresh = startupAgent("dsh-fresh");
    await handlers.get("agent/created")({ agent: fresh, source: "startup" });
    assert.equal((await preStepTexts(handlers, fresh)).some(historyNote), false);
  });
});

test("sessionHistoryPointer false or capture off keeps the note out", async () => {
  for (const input of [{ sessionHistoryPointer: false }, { autoCapture: false }]) {
    const { handlers } = mountPlugin(input);
    const agent = startupAgent("dsh-quiet");
    await withQuietFetch(async () => {
      await handlers.get("agent/created")({ agent, source: "resume" });
      handlers.get("session/event")(agent.session, { type: "compaction/end", data: { turn: 1 } });
      assert.equal((await preStepTexts(handlers, agent)).some(historyNote), false);
    });
  }
});

test("tools/execute fills session_history's session for main sessions only", async () => {
  const calls = [];
  const tools = {
    register() {},
    get(name) {
      return name === "mcp__openviking__session_history"
        ? { execute: async args => (calls.push(args), { content: [{ type: "text", text: "ok" }] }) }
        : undefined;
    },
  };
  const { handlers } = mountPlugin({ skipSubagentSessions: true }, tools);
  const execute = handlers.get("tools/execute");
  const next = async () => ({ isError: false, value: "body", content: [] });
  const exec = session => ({
    name: "mcp__openviking__session_history",
    arguments: Object.freeze({ action: "overview" }),
    agent: { session },
    signal: new AbortController().signal,
  });

  const filled = await execute(exec({ id: "main", header: {} }), next);
  assert.deepEqual(calls, [{ action: "overview", session: "dsh-main" }]);
  assert.equal(filled.isError, false);

  const child = await execute(exec({ id: "child", header: { origin: "subagent" } }), next);
  assert.equal(child.value, "body");
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
