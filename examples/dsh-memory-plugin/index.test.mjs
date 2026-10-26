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

function mountPlugin() {
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
    tools: { register() {} },
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
