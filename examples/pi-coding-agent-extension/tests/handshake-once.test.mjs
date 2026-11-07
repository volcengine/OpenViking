/** Exercise the extension lifecycle with REST available and MCP refused. */

import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { createServer } from "node:http";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { enqueue, listPending } from "../shared/pending-queue.mjs";

const EXTENSION_DIR = dirname(dirname(fileURLToPath(import.meta.url)));
const EXTENSION_URL = pathToFileURL(EXTENSION_DIR + "/").href;

// Only the extension's own relative `.js` specifiers are touched, and only
// after the real resolution has already failed, so nothing else in the process
// can change meaning because this file ran.
registerHooks({
  resolve(specifier, context, nextResolve) {
    const inExtension = typeof context?.parentURL === "string"
      && context.parentURL.startsWith(EXTENSION_URL);
    if (!inExtension || !specifier.startsWith(".") || !specifier.endsWith(".js")) {
      return nextResolve(specifier, context);
    }
    try {
      return nextResolve(specifier, context);
    } catch {
      return nextResolve(specifier.slice(0, -3) + ".ts", context);
    }
  },
});

const MANAGED_ENV = [
  "OPENVIKING_CREDENTIAL_SOURCE",
  "OPENVIKING_CLI_CONFIG_FILE",
  "OPENVIKING_CONFIG_FILE",
  "OPENVIKING_URL",
  "OPENVIKING_BASE_URL",
  "OPENVIKING_MCP_URL",
  "OPENVIKING_API_KEY",
  "OPENVIKING_BEARER_TOKEN",
  "OPENVIKING_ACCOUNT",
  "OPENVIKING_USER",
  "OPENVIKING_AUTH_MODE",
  "OPENVIKING_PEER_ID",
  "OPENVIKING_DEBUG",
  "OPENVIKING_DEBUG_LOG",
  "OV_DEBUG_LOG",
  "OPENVIKING_PENDING_DIR",
];

async function startServer() {
  const initializes = [];
  const requests = [];
  const gate = { health: null, replay: null, messagesStatus: 200, pendingTokens: 0 };
  const server = createServer((req, res) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", async () => {
      const path = (req.url || "").split("?")[0];
      requests.push(`${req.method} ${path}`);
      if (path === "/mcp") {
        let method = null;
        try {
          method = JSON.parse(Buffer.concat(chunks).toString("utf8"))?.method ?? null;
        } catch {
          // A malformed body is still a post worth ignoring, not a crash.
        }
        if (method === "initialize") initializes.push(Date.now());
        res.writeHead(403, { "content-type": "application/json" });
        res.end(JSON.stringify({ detail: "root key cannot reach /mcp" }));
        return;
      }
      if (path === "/health") await gate.health;
      if (/\/messages(?:\/batch)?$/.test(path)) {
        await gate.replay;
        if (gate.messagesStatus !== 200) {
          res.writeHead(gate.messagesStatus, { "content-type": "application/json" });
          res.end(JSON.stringify({ detail: "replay unavailable" }));
          return;
        }
      }
      if (req.method === "GET" && /^\/api\/v1\/sessions\/[^/]+$/.test(path)) {
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ status: "ok", result: { pending_tokens: gate.pendingTokens } }));
        return;
      }
      // Everything the REST half touches during startup: /health, the profile
      // fetch, recall. An empty result keeps each of them a no-op.
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ status: "ok", result: {} }));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return {
    url: `http://127.0.0.1:${port}`,
    initializes,
    requests,
    gate,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

/** The slice of pi's API `index.ts` reaches for. */
function fakePi() {
  const handlers = new Map();
  const tools = [];
  const commands = new Map();
  const entries = [];
  return {
    pi: {
      on(event, handler) { handlers.set(event, handler); },
      registerTool(tool) { tools.push(tool?.name); },
      registerCommand(name, command) { commands.set(name, command); },
      appendEntry(type, data) { entries.push({ type, data }); },
      getAllTools() { return []; },
      sendMessage() {},
    },
    handlers,
    tools,
    commands,
    entries,
  };
}

function fakeCtx(notified = [], statuses = []) {
  return {
    sessionManager: {
      getSessionId: () => "pi-session-handshake",
      getBranch: () => [],
      getLeafId: () => "entry-1",
    },
    ui: {
      notify: (...args) => notified.push(args),
      setStatus: (...args) => statuses.push(args),
    },
    getContextUsage: () => ({ tokens: 0, contextWindow: 262144, percent: 0 }),
  };
}

/** Boot one extension instance against a throwaway config and a fake server. */
async function withExtension(t, fn, plugin = {}) {
  const server = await startServer();
  const dir = await mkdtemp(join(tmpdir(), "ov-pi-handshake-"));
  const saved = Object.fromEntries(MANAGED_ENV.map((name) => [name, process.env[name]]));
  for (const name of MANAGED_ENV) delete process.env[name];
  process.env.OPENVIKING_CREDENTIAL_SOURCE = "env";
  process.env.OPENVIKING_URL = server.url;
  process.env.OPENVIKING_API_KEY = "pi-handshake-key";
  process.env.OPENVIKING_CLI_CONFIG_FILE = join(dir, "ovcli.conf");
  process.env.OPENVIKING_CONFIG_FILE = join(dir, "ov.conf");
  process.env.OPENVIKING_PENDING_DIR = join(dir, "pending");
  await writeFile(join(dir, "ovcli.conf"), JSON.stringify({ plugin: { pi: plugin } }), "utf8");

  t.after(async () => {
    for (const [name, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
    await server.close();
    await rm(dir, { recursive: true, force: true });
  });

  // Dynamic, so the resolve hook above is already installed. The module is
  // evaluated once per process; each call to its default export builds a fresh
  // extension instance with its own bridge and its own session state.
  const mod = await import(pathToFileURL(join(EXTENSION_DIR, "index.ts")).href);
  assert.equal(typeof mod.default, "function");

  const { pi, handlers, tools, commands, entries } = fakePi();
  await mod.default(pi);
  const notified = [];
  const statuses = [];
  return fn({
    server,
    handlers,
    tools,
    commands,
    entries,
    notified,
    statuses,
    ctx: fakeCtx(notified, statuses),
    makeCtx: () => fakeCtx(notified, statuses),
    configPath: join(dir, "ovcli.conf"),
    turn: (prompt) => handlers.get("before_agent_start")(
      { type: "before_agent_start", prompt, systemPrompt: "BASE" },
      fakeCtx(notified, statuses),
    ),
    shutdown: () => handlers.get("session_shutdown")(
      { type: "session_shutdown", reason: "reload" },
      fakeCtx(notified, statuses),
    ),
  });
}

test("the first turn attempts the handshake once, and the retry waits for the next one", async (t) => {
  await withExtension(t, async ({ server, turn, shutdown, notified }) => {
    await turn("hello");
    assert.equal(
      server.initializes.length,
      1,
      "turn one must not re-attempt the handshake start() already joined",
    );

    await turn("again");
    assert.equal(
      server.initializes.length,
      2,
      "turn two is the retry branch doing its job on a toolless session",
    );

    await turn("and again");
    assert.equal(server.initializes.length, 3, "and it keeps retrying while there are no tools");

    // The failure is announced once, not once per attempt.
    const warnings = notified.filter(([message]) => /no tools this session/.test(message));
    assert.equal(warnings.length, 1, JSON.stringify(notified));
    assert.match(warnings[0][0], /403/);

    await shutdown();
  });
});

test("a refused handshake still leaves the session working", async (t) => {
  await withExtension(t, async ({ turn, tools, statuses, shutdown }) => {
    const result = await turn("hello");
    // No tools registered, but the session is live: the system prompt still
    // comes back (or at worst undefined), and the status line flags the gap.
    assert.deepEqual(tools, []);
    if (result) assert.ok(!/OpenViking tools/.test(result.systemPrompt));
    assert.ok(
      statuses.some(([, status]) => /OV ✓/.test(status) && /tools ✗/.test(status)),
      JSON.stringify(statuses),
    );
    await shutdown();
  });
});

test("an abort-on-settle host can recall on the first turn", async (t) => {
  await withExtension(t, async ({ handlers, server, makeCtx, shutdown }) => {
    const controller = new AbortController();
    const startupCtx = makeCtx();
    startupCtx.signal = controller.signal;
    startupCtx.ui.notify = () => {
      controller.signal.throwIfAborted();
    };
    startupCtx.ui.setStatus = () => {
      controller.signal.throwIfAborted();
    };

    await handlers.get("session_start")({ type: "session_start" }, startupCtx);
    // PI-Desktop retires every ctx bridge method as soon as its handler settles.
    controller.abort();
    await handlers.get("before_agent_start")(
      { prompt: "desktop startup recall probe", systemPrompt: "BASE" },
      makeCtx(),
    );
    await handlers.get("context")(
      { messages: [{ role: "user", content: "desktop startup recall probe" }] },
      makeCtx(),
    );
    assert.ok(server.requests.some((request) => /search/.test(request)), server.requests.join("\n"));
    await shutdown();
  });
});

test("a scoped first turn recalls while current-session replay is still pending", async (t) => {
  await withExtension(t, async ({ handlers, commands, statuses, notified, server, makeCtx, shutdown }) => {
    await enqueue("addMessage", "pi-pi-session-handshake", {
      role: "user", content: "queued before this session",
    });
    let releaseReplay;
    server.gate.replay = new Promise((resolve) => { releaseReplay = resolve; });
    const controller = new AbortController();
    const startupCtx = makeCtx();
    startupCtx.signal = controller.signal;
    const turn = (async () => {
      await handlers.get("session_start")({ type: "session_start" }, startupCtx);
      controller.abort();
      await handlers.get("before_agent_start")(
        { prompt: "slow replay recall probe", systemPrompt: "BASE" }, makeCtx(),
      );
      await handlers.get("context")(
        { messages: [{ role: "user", content: "slow replay recall probe" }] }, makeCtx(),
      );
    })();
    let timer;
    let outcome;
    try {
      outcome = await Promise.race([
        turn.then(() => "ready"),
        new Promise((resolve) => { timer = setTimeout(() => resolve("blocked"), 800); }),
      ]);
      if (outcome === "ready") {
        const captureCtx = makeCtx();
        captureCtx.sessionManager.getBranch = () => [
          { id: "queued-turn", type: "message", message: { role: "user", content: "Capture this new turn after replay finishes." } },
        ];
        await handlers.get("turn_end")({ type: "turn_end" }, captureCtx);
        assert.match(statuses.at(-1)[1], /replay/);
        assert.equal(server.requests.filter((request) => /\/messages(?:\/batch)?$/.test(request)).length, 1);
        await commands.get("viking").handler("commit", captureCtx);
        assert.ok(notified.some(([message]) => /replay/.test(message)), JSON.stringify(notified));
        const beforeCompact = server.requests.length;
        assert.equal(await handlers.get("session_before_compact")(
          { preparation: { firstKeptEntryId: "queued-turn", tokensBefore: 100 } }, captureCtx,
        ), undefined);
        assert.equal(server.requests.length, beforeCompact);
      }
    } finally {
      clearTimeout(timer);
      releaseReplay();
    }
    await turn;
    assert.equal(outcome, "ready", "first-turn recall must not wait for old queued writes");
    assert.ok(server.requests.some((request) => /search/.test(request)), server.requests.join("\n"));
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.ok(server.requests.filter((request) => /\/messages(?:\/batch)?$/.test(request)).length >= 2,
      server.requests.join("\n"));
    await shutdown();
  }, { mcpEnabled: false, takeoverEnabled: true });
});

test("shutdown during replay keeps the completed turn durable and blocks takeover", async (t) => {
  await withExtension(t, async ({ handlers, server, entries, makeCtx, shutdown }) => {
    await enqueue("addMessage", "pi-pi-session-handshake", {
      role: "user", content: "older queued turn",
    });
    let releaseReplay;
    server.gate.replay = new Promise((resolve) => { releaseReplay = resolve; });
    try {
      const controller = new AbortController();
      const startupCtx = makeCtx();
      startupCtx.signal = controller.signal;
      await handlers.get("session_start")({ type: "session_start" }, startupCtx);
      controller.abort();
      await handlers.get("before_agent_start")(
        { prompt: "capture before shutdown", systemPrompt: "BASE" }, makeCtx(),
      );
      const captureCtx = makeCtx();
      captureCtx.sessionManager.getBranch = () => [
        { id: "shutdown-turn", type: "message", message: { role: "user", content: "Keep this turn after shutdown during replay." } },
      ];
      await handlers.get("turn_end")({ type: "turn_end" }, captureCtx);
      assert.ok((await listPending()).some(({ entry }) =>
        JSON.stringify(entry.payload).includes("Keep this turn after shutdown")));

      await shutdown();
      assert.ok((await listPending()).some(({ entry }) =>
        JSON.stringify(entry.payload).includes("Keep this turn after shutdown")));
      assert.ok(entries.some(({ data }) => data?.captureGap === true));
      assert.equal(server.requests.filter((request) => /\/commit$/.test(request)).length, 0);
    } finally {
      releaseReplay();
    }
  }, { mcpEnabled: false, takeoverEnabled: true });
});

test("shutdown during replay leaves ordinary capture queued without a premature commit", async (t) => {
  await withExtension(t, async ({ handlers, server, makeCtx, shutdown }) => {
    await enqueue("addMessage", "pi-pi-session-handshake", {
      role: "user", content: "old pending message",
    });
    let releaseReplay;
    server.gate.replay = new Promise((resolve) => { releaseReplay = resolve; });
    try {
      const controller = new AbortController();
      const startupCtx = makeCtx();
      startupCtx.signal = controller.signal;
      await handlers.get("session_start")({ type: "session_start" }, startupCtx);
      controller.abort();
      await handlers.get("before_agent_start")(
        { prompt: "ordinary shutdown capture", systemPrompt: "BASE" }, makeCtx(),
      );
      const captureCtx = makeCtx();
      captureCtx.sessionManager.getBranch = () => [
        { id: "ordinary-shutdown", type: "message", message: { role: "user", content: "Persist an ordinary turn before shutdown." } },
      ];
      await handlers.get("turn_end")({ type: "turn_end" }, captureCtx);
      await shutdown();
      const queued = await listPending();
      assert.ok(queued.some(({ entry }) => entry.type === "addMessage"
        && JSON.stringify(entry.payload).includes("Persist an ordinary turn")));
      assert.ok(!queued.some(({ entry }) => entry.type === "commitSession"));
      assert.equal(server.requests.filter((request) => /\/commit$/.test(request)).length, 0);
    } finally {
      releaseReplay();
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(server.requests.filter((request) => /\/commit$/.test(request)).length, 0);
  }, { mcpEnabled: false, takeoverEnabled: false });
});

test("ordinary shutdown during replay persists a conservative gap across restart", async (t) => {
  await withExtension(t, async ({ handlers, commands, entries, server, makeCtx, shutdown }) => {
    await enqueue("addMessage", "pi-pi-session-handshake", {
      role: "user", content: "older pending before shutdown",
    });
    let releaseReplay;
    server.gate.replay = new Promise((resolve) => { releaseReplay = resolve; });
    try {
      await handlers.get("before_agent_start")(
        { prompt: "shutdown with replay in flight", systemPrompt: "BASE" }, makeCtx(),
      );
      await shutdown();
      assert.ok(entries.some(({ data }) => data?.captureGap === true));
      assert.equal(server.requests.filter((request) => /\/commit$/.test(request)).length, 0);
    } finally {
      releaseReplay();
    }
    for (let i = 0; i < 100 && (await listPending()).length > 0; i++) {
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.equal((await listPending()).length, 0, "old replay can complete after shutdown");

    // A later process can still deliver queued and newly captured messages,
    // but cannot claim a complete archive for this session after an
    // unobserved shutdown/replay race.
    await enqueue("addMessage", "pi-pi-session-handshake", {
      role: "user", content: "pending for next process",
    });
    const { default: extension } = await import(pathToFileURL(join(EXTENSION_DIR, "index.ts")).href);
    const second = fakePi();
    await extension(second.pi);
    const notified = [];
    const statuses = [];
    const secondCtx = fakeCtx(notified, statuses);
    const newTurn = { id: "post-shutdown-turn", type: "message", message: { role: "user", content: "capture after restart" } };
    secondCtx.sessionManager.getBranch = () => [
      ...entries.filter(({ data }) => data?.captureGap === true).map(({ type, data }, index) => ({
        id: `gap-${index}`, type: "custom", customType: type, data,
      })),
      newTurn,
    ];
    server.gate.pendingTokens = 2000;
    await second.handlers.get("before_agent_start")(
      { prompt: "resume same session", systemPrompt: "BASE" }, secondCtx,
    );
    let replayStatus = "";
    for (let i = 0; i < 100; i++) {
      await second.commands.get("viking").handler("", secondCtx);
      replayStatus = notified.at(-1)?.[0] || "";
      if (!/replay: pending/.test(replayStatus)) break;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.doesNotMatch(replayStatus, /replay:/);
    assert.equal((await listPending()).length, 0, "the next process still replays pending messages");
    const capturesBeforeTurn = server.requests.filter((request) => /\/messages\/batch$/.test(request)).length;
    await second.handlers.get("turn_end")({ type: "turn_end" }, secondCtx);
    assert.equal(server.requests.filter((request) => /\/messages\/batch$/.test(request)).length, capturesBeforeTurn + 1);
    await second.commands.get("viking").handler("commit", secondCtx);
    assert.ok(notified.some(([message]) => /cannot commit — capture incomplete/.test(message)));
    await second.handlers.get("session_shutdown")({}, secondCtx);
    assert.equal(server.requests.filter((request) => /\/commit$/.test(request)).length, 0);
  }, { mcpEnabled: false, takeoverEnabled: false, commitTokenThreshold: 1000 });
});

test("a replay rejected after ordinary shutdown cannot be committed after restart", async (t) => {
  await withExtension(t, async ({ handlers, entries, server, makeCtx, shutdown }) => {
    await enqueue("addMessage", "pi-pi-session-handshake", {
      role: "user", content: "reject only after shutdown",
    });
    let releaseReplay;
    server.gate.replay = new Promise((resolve) => { releaseReplay = resolve; });
    try {
      await handlers.get("before_agent_start")(
        { prompt: "replay still in flight", systemPrompt: "BASE" }, makeCtx(),
      );
      await shutdown();
      server.gate.messagesStatus = 400;
    } finally {
      releaseReplay();
    }
    for (let i = 0; i < 100 && (await listPending()).length > 0; i++) {
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.equal((await listPending()).length, 0, "the rejected queue file is gone");

    const { default: extension } = await import(pathToFileURL(join(EXTENSION_DIR, "index.ts")).href);
    const second = fakePi();
    await extension(second.pi);
    const notified = [];
    const secondCtx = fakeCtx(notified);
    secondCtx.sessionManager.getBranch = () => entries.map(({ type, data }, index) => ({
      id: `gap-${index}`, type: "custom", customType: type, data,
    }));
    await second.handlers.get("before_agent_start")(
      { prompt: "restart after rejection", systemPrompt: "BASE" }, secondCtx,
    );
    await second.commands.get("viking").handler("commit", secondCtx);
    assert.ok(notified.some(([message]) => /cannot commit — capture incomplete/.test(message)));
    await second.handlers.get("session_shutdown")({}, secondCtx);
    assert.equal(server.requests.filter((request) => /\/commit$/.test(request)).length, 0);
  }, { mcpEnabled: false, takeoverEnabled: false });
});

test("ordinary capture and shutdown commit resume after replay settles", async (t) => {
  await withExtension(t, async ({ handlers, commands, notified, server, makeCtx, shutdown }) => {
    await enqueue("addMessage", "pi-pi-session-handshake", {
      role: "user", content: "old pending message",
    });
    let releaseReplay;
    server.gate.replay = new Promise((resolve) => { releaseReplay = resolve; });
    try {
      await handlers.get("before_agent_start")(
        { prompt: "resume after replay", systemPrompt: "BASE" }, makeCtx(),
      );
    } finally {
      releaseReplay();
    }
    const ctx = makeCtx();
    let status = "";
    for (let i = 0; i < 100; i++) {
      await commands.get("viking").handler("", ctx);
      status = notified.at(-1)?.[0] || "";
      if (!/replay: pending/.test(status)) break;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.ok(!/replay: pending/.test(status), status);
    ctx.sessionManager.getBranch = () => [
      { id: "post-replay", type: "message", message: { role: "user", content: "Capture after old replay finished." } },
    ];
    await handlers.get("turn_end")({ type: "turn_end" }, ctx);
    await shutdown();
    const requestPaths = server.requests;
    const captureIndex = requestPaths.findIndex((request) => /\/messages\/batch$/.test(request));
    const commitIndex = requestPaths.findIndex((request) => /\/commit$/.test(request));
    assert.ok(captureIndex >= 0 && commitIndex > captureIndex, requestPaths.join("\n"));
  }, { mcpEnabled: false, takeoverEnabled: false });
});

test("replay delivery failure stays visible and blocks an incomplete commit", async (t) => {
  await withExtension(t, async ({ handlers, commands, notified, statuses, server, makeCtx, shutdown }) => {
    await enqueue("addMessage", "pi-pi-session-handshake", {
      role: "user", content: "retry this queued message",
    });
    server.gate.messagesStatus = 503;
    const ctx = makeCtx();
    await handlers.get("before_agent_start")(
      { prompt: "replay failure status", systemPrompt: "BASE" }, ctx,
    );
    let status = "";
    for (let i = 0; i < 100; i++) {
      await commands.get("viking").handler("", ctx);
      status = notified.at(-1)?.[0] || "";
      if (/replay: failed/.test(status)) break;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.match(status, /replay: failed/);
    await handlers.get("before_agent_start")(
      { prompt: "replay failure again", systemPrompt: "BASE" }, ctx,
    );
    assert.match(statuses.at(-1)[1], /replay ✗/);
    await commands.get("viking").handler("commit", ctx);
    assert.ok(notified.some(([message]) => /cannot commit — pending replay failed/.test(message)));
    await shutdown();
  }, { mcpEnabled: false, takeoverEnabled: false });
});

test("a dropped replay message records capture gap before takeover resumes", async (t) => {
  await withExtension(t, async ({ handlers, commands, entries, notified, server, makeCtx, shutdown }) => {
    await enqueue("addMessage", "pi-pi-session-handshake", {
      role: "user", content: "old message that the server rejects",
    });
    server.gate.messagesStatus = 400;
    const ctx = makeCtx();
    await handlers.get("before_agent_start")(
      { prompt: "replay dropped message", systemPrompt: "BASE" }, ctx,
    );
    let status = "";
    for (let i = 0; i < 100; i++) {
      await commands.get("viking").handler("", ctx);
      status = notified.at(-1)?.[0] || "";
      if (!/replay: pending/.test(status)) break;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.ok(!/replay: pending/.test(status), status);
    assert.ok(entries.some(({ data }) => data?.captureGap === true));
    await shutdown();
  }, { mcpEnabled: false, takeoverEnabled: true });
});

test("queue failure during replay persists a takeover capture gap", async (t) => {
  await withExtension(t, async ({ handlers, server, entries, configPath, makeCtx, shutdown }) => {
    await enqueue("addMessage", "pi-pi-session-handshake", {
      role: "user", content: "old pending message",
    });
    let releaseReplay;
    server.gate.replay = new Promise((resolve) => { releaseReplay = resolve; });
    try {
      const controller = new AbortController();
      const startupCtx = makeCtx();
      startupCtx.signal = controller.signal;
      await handlers.get("session_start")({ type: "session_start" }, startupCtx);
      controller.abort();
      await handlers.get("before_agent_start")(
        { prompt: "capture gap probe", systemPrompt: "BASE" }, makeCtx(),
      );
      const blockedParent = join(dirname(configPath), "not-a-directory");
      await writeFile(blockedParent, "");
      const priorPendingDir = process.env.OPENVIKING_PENDING_DIR;
      process.env.OPENVIKING_PENDING_DIR = join(blockedParent, "pending");
      try {
        const captureCtx = makeCtx();
        captureCtx.sessionManager.getBranch = () => [
          { id: "failed-queue", type: "message", message: { role: "user", content: "This message cannot be queued." } },
        ];
        await handlers.get("turn_end")({ type: "turn_end" }, captureCtx);
        assert.ok(entries.some(({ data }) => data?.captureGap === true));
      } finally {
        process.env.OPENVIKING_PENDING_DIR = priorPendingDir;
      }
      await shutdown();
    } finally {
      releaseReplay();
    }
  }, { mcpEnabled: false, takeoverEnabled: true });
});

test("queue failure during replay blocks an incomplete ordinary commit", async (t) => {
  await withExtension(t, async ({ handlers, commands, notified, statuses, server, configPath, makeCtx, shutdown }) => {
    await enqueue("addMessage", "pi-pi-session-handshake", {
      role: "user", content: "old pending message",
    });
    let releaseReplay;
    server.gate.replay = new Promise((resolve) => { releaseReplay = resolve; });
    try {
      await handlers.get("before_agent_start")(
        { prompt: "ordinary capture gap", systemPrompt: "BASE" }, makeCtx(),
      );
      const blockedParent = join(dirname(configPath), "not-a-directory");
      await writeFile(blockedParent, "");
      const priorPendingDir = process.env.OPENVIKING_PENDING_DIR;
      process.env.OPENVIKING_PENDING_DIR = join(blockedParent, "pending");
      try {
        const captureCtx = makeCtx();
        captureCtx.sessionManager.getBranch = () => [
          { id: "lost-ordinary-turn", type: "message", message: { role: "user", content: "This ordinary message cannot be queued." } },
        ];
        await handlers.get("turn_end")({ type: "turn_end" }, captureCtx);
      } finally {
        process.env.OPENVIKING_PENDING_DIR = priorPendingDir;
      }
    } finally {
      releaseReplay();
    }
    const ctx = makeCtx();
    let status = "";
    for (let i = 0; i < 100; i++) {
      await commands.get("viking").handler("", ctx);
      status = notified.at(-1)?.[0] || "";
      if (!/replay: pending/.test(status)) break;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.match(status, /capture: incomplete/);
    await handlers.get("before_agent_start")(
      { prompt: "ordinary capture gap status", systemPrompt: "BASE" }, ctx,
    );
    assert.match(statuses.at(-1)[1], /capture ✗/);
    await commands.get("viking").handler("commit", ctx);
    assert.ok(notified.some(([message]) => /cannot commit — capture incomplete/.test(message)));
    await shutdown();
    assert.equal(server.requests.filter((request) => /\/commit$/.test(request)).length, 0);
  }, { mcpEnabled: false, takeoverEnabled: false });
});

test("ordinary capture gap survives extension restart and blocks all commits", async (t) => {
  await withExtension(t, async ({ handlers, entries, server, configPath, makeCtx, shutdown }) => {
    await enqueue("addMessage", "pi-pi-session-handshake", {
      role: "user", content: "older pending message",
    });
    let releaseReplay;
    server.gate.replay = new Promise((resolve) => { releaseReplay = resolve; });
    const firstTurn = { id: "lost-turn", type: "message", message: { role: "user", content: "This turn cannot be queued." } };
    try {
      await handlers.get("before_agent_start")(
        { prompt: "first process", systemPrompt: "BASE" }, makeCtx(),
      );
      const blockedParent = join(dirname(configPath), "blocked-pending-parent");
      await writeFile(blockedParent, "");
      const priorPendingDir = process.env.OPENVIKING_PENDING_DIR;
      process.env.OPENVIKING_PENDING_DIR = join(blockedParent, "pending");
      try {
        const firstCtx = makeCtx();
        firstCtx.sessionManager.getBranch = () => [firstTurn];
        await handlers.get("turn_end")({ type: "turn_end" }, firstCtx);
      } finally {
        process.env.OPENVIKING_PENDING_DIR = priorPendingDir;
      }
      await shutdown();
    } finally {
      releaseReplay();
    }
    const persisted = entries.filter(({ data }) => data?.captureGap === true);
    assert.ok(persisted.length > 0, "a lost ordinary turn must persist a capture gap");

    const { default: extension } = await import(pathToFileURL(join(EXTENSION_DIR, "index.ts")).href);
    const second = fakePi();
    await extension(second.pi);
    const notified = [];
    const statuses = [];
    const secondCtx = fakeCtx(notified, statuses);
    secondCtx.sessionManager.getBranch = () => [
      firstTurn,
      ...persisted.map(({ type, data }, index) => ({
        id: `gap-${index}`, type: "custom", customType: type, data,
      })),
    ];
    await second.handlers.get("before_agent_start")(
      { prompt: "same session, second process", systemPrompt: "BASE" }, secondCtx,
    );
    assert.match(statuses.at(-1)[1], /capture ✗/);
    await second.commands.get("viking").handler("commit", secondCtx);
    assert.ok(notified.some(([message]) => /cannot commit — capture incomplete/.test(message)));
    await second.handlers.get("session_shutdown")({}, secondCtx);
    assert.equal(server.requests.filter((request) => /\/commit$/.test(request)).length, 0);
  }, { mcpEnabled: false, takeoverEnabled: false });
});

test("ordinary replay drop blocks commit of an incomplete prior session", async (t) => {
  await withExtension(t, async ({ handlers, commands, notified, server, makeCtx, shutdown }) => {
    await enqueue("addMessage", "pi-pi-session-handshake", {
      role: "user", content: "old message rejected permanently",
    });
    server.gate.messagesStatus = 400;
    const ctx = makeCtx();
    await handlers.get("before_agent_start")(
      { prompt: "ordinary replay drop", systemPrompt: "BASE" }, ctx,
    );
    let status = "";
    for (let i = 0; i < 100; i++) {
      await commands.get("viking").handler("", ctx);
      status = notified.at(-1)?.[0] || "";
      if (!/replay: pending/.test(status)) break;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.match(status, /capture: incomplete/);
    await commands.get("viking").handler("commit", ctx);
    assert.ok(notified.some(([message]) => /cannot commit — capture incomplete/.test(message)));
    await shutdown();
    assert.equal(server.requests.filter((request) => /\/commit$/.test(request)).length, 0);
  }, { mcpEnabled: false, takeoverEnabled: false });
});

test("an empty startup branch is not a capability failure, but an empty completed turn is", async (t) => {
  await withExtension(t, async ({ handlers, commands, statuses, notified, server, makeCtx, shutdown }) => {
    await handlers.get("before_agent_start")(
      { prompt: "desktop branch capability", systemPrompt: "BASE" },
      makeCtx(),
    );
    assert.ok(statuses.some(([, status]) => /OV ✓/.test(status) && !/capture ✗/.test(status)));

    await handlers.get("turn_end")({ type: "turn_end" }, makeCtx());
    assert.ok(statuses.some(([, status]) => /capture ✗/.test(status)), JSON.stringify(statuses));

    await commands.get("viking").handler("", makeCtx());
    assert.ok(notified.some(([message]) => /capture: unavailable/.test(message)), JSON.stringify(notified));

    for (let i = 0; i < 100; i++) {
      await commands.get("viking").handler("", makeCtx());
      if (!/replay: pending/.test(notified.at(-1)?.[0] || "")) break;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }

    await commands.get("viking").handler("commit", makeCtx());
    assert.ok(!notified.some(([message]) => /cannot commit/.test(message)), JSON.stringify(notified));
    assert.ok(server.requests.some((request) => /commit/.test(request)), server.requests.join("\n"));

    await handlers.get("before_agent_start")(
      { prompt: "recall after capture unavailable", systemPrompt: "BASE" },
      makeCtx(),
    );
    await handlers.get("context")(
      { messages: [{ role: "user", content: "recall after capture unavailable" }] },
      makeCtx(),
    );
    assert.ok(server.requests.some((request) => /search/.test(request)), server.requests.join("\n"));

    const recoveredCtx = makeCtx();
    recoveredCtx.sessionManager.getBranch = () => [
      { id: "entry-recovered", type: "message", message: { role: "user", content: "ok" } },
    ];
    await handlers.get("turn_end")({ type: "turn_end" }, recoveredCtx);
    assert.ok(!/capture ✗/.test(statuses.at(-1)[1]), statuses.at(-1)[1]);
    await shutdown();
  });
});

test("takeover reports a stubbed branch and leaves native compaction alone", async (t) => {
  await withExtension(t, async ({ server, configPath, shutdown }) => {
    await shutdown();
    await writeFile(configPath, JSON.stringify({
      plugin: { pi: { mcpEnabled: false, takeoverEnabled: true } },
    }));
    const { default: extension } = await import(pathToFileURL(join(EXTENSION_DIR, "index.ts")).href);
    const { pi, handlers, commands } = fakePi();
    await extension(pi);
    const statuses = [];
    const notified = [];
    const ctx = fakeCtx(notified, statuses);
    await handlers.get("before_agent_start")(
      { prompt: "takeover branch probe", systemPrompt: "BASE" }, ctx,
    );
    for (let i = 0; i < 100; i++) {
      await commands.get("viking").handler("", ctx);
      if (!/replay: pending/.test(notified.at(-1)?.[0] || "")) break;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    await handlers.get("turn_end")({ type: "turn_end" }, ctx);
    assert.match(statuses.at(-1)[1], /capture ✗ · takeover ✗/);
    await commands.get("viking").handler("commit", ctx);
    assert.ok(notified.some(([message]) => /session history unavailable/.test(message)), JSON.stringify(notified));
    const requestsBeforeCompaction = server.requests.length;
    assert.equal(await handlers.get("session_before_compact")(
      { preparation: { firstKeptEntryId: "entry-1", tokensBefore: 100 } }, ctx,
    ), undefined);
    assert.equal(server.requests.length, requestsBeforeCompaction);
    const recoveredCtx = fakeCtx(notified, statuses);
    recoveredCtx.sessionManager.getBranch = () => [
      { id: "entry-1", type: "message", message: { role: "user", content: "Recovered history." } },
    ];
    await handlers.get("session_before_compact")({ preparation: {} }, recoveredCtx);
    assert.ok(!/capture ✗/.test(statuses.at(-1)[1]), statuses.at(-1)[1]);
    await handlers.get("session_shutdown")({}, ctx);
  });
});

test("reload clears only the owning extension's coexistence marker", async (t) => {
  await withExtension(t, async ({ turn, shutdown }) => {
    await turn("hello");
    assert.ok(globalThis.__OPENVIKING_PI_EXTENSION__);
    await shutdown();
    assert.equal(globalThis.__OPENVIKING_PI_EXTENSION__, undefined);
  });
  await withExtension(t, async ({ turn, shutdown }) => {
    await turn("hello");
    const replacement = { dir: "another-instance" };
    globalThis.__OPENVIKING_PI_EXTENSION__ = replacement;
    await shutdown();
    assert.equal(globalThis.__OPENVIKING_PI_EXTENSION__, replacement);
    delete globalThis.__OPENVIKING_PI_EXTENSION__;
  });
});

test("shutdown during health check cannot restore the marker or register tools", async (t) => {
  await withExtension(t, async ({ server, turn, shutdown, tools }) => {
    let release;
    server.gate.health = new Promise((resolve) => { release = resolve; });
    const starting = turn("hello");
    await shutdown();
    release();
    await starting;
    assert.equal(globalThis.__OPENVIKING_PI_EXTENSION__, undefined);
    assert.deepEqual(tools, []);
    assert.equal(server.initializes.length, 0);
  });
});

for (const plugin of [{ mcpEnabled: false }, { bypassSession: true }]) {
  test("MCP stays off for " + JSON.stringify(plugin), async (t) => {
    await withExtension(t, async ({ server, turn, shutdown, configPath }) => {
      // The instance loads configuration at construction, so reload it first.
      await shutdown();
      await writeFile(configPath, JSON.stringify({ plugin: { pi: plugin } }));
      const { default: extension } = await import(pathToFileURL(join(EXTENSION_DIR, "index.ts")).href);
      const { pi, handlers } = fakePi();
      await extension(pi);
      await handlers.get("before_agent_start")({ prompt: "hello", systemPrompt: "BASE" }, fakeCtx());
      assert.equal(server.initializes.length, 0);
      await handlers.get("session_shutdown")({}, fakeCtx());
    });
  });
}

for (const plugin of [{ autoCapture: false }, { autoCapture: false, takeoverEnabled: true }]) {
  test("compaction and shutdown do not write with " + JSON.stringify(plugin), async (t) => {
    await withExtension(t, async ({ server, shutdown, configPath }) => {
      await shutdown();
      await writeFile(configPath, JSON.stringify({ plugin: { pi: { ...plugin, mcpEnabled: false } } }));
      const { default: extension } = await import(pathToFileURL(join(EXTENSION_DIR, "index.ts")).href);
      const { pi, handlers } = fakePi();
      await extension(pi);
      await handlers.get("before_agent_start")({ prompt: "hello", systemPrompt: "BASE" }, fakeCtx());
      server.requests.length = 0;
      const compaction = await handlers.get("session_before_compact")(
        { type: "session_before_compact", preparation: { firstKeptEntryId: "entry-1", tokensBefore: 1 } },
        fakeCtx(),
      );
      assert.equal(compaction, undefined, "pi keeps its own compaction");
      await handlers.get("session_shutdown")({ type: "session_shutdown", reason: "quit" }, fakeCtx());
      assert.deepEqual(server.requests.filter((r) => r.startsWith("POST")), []);
    });
  });
}
