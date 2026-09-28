/** Exercise the extension lifecycle with REST available and MCP refused. */

import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { createServer } from "node:http";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

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
  const gate = { health: null };
  const server = createServer((req, res) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", async () => {
      const path = (req.url || "").split("?")[0];
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
    gate,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

/** The slice of pi's API `index.ts` reaches for. */
function fakePi() {
  const handlers = new Map();
  const tools = [];
  return {
    pi: {
      on(event, handler) { handlers.set(event, handler); },
      registerTool(tool) { tools.push(tool?.name); },
      registerCommand() {},
      appendEntry() {},
      getAllTools() { return []; },
      sendMessage() {},
    },
    handlers,
    tools,
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
async function withExtension(t, fn) {
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
  await writeFile(join(dir, "ovcli.conf"), JSON.stringify({ plugin: { pi: {} } }), "utf8");

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
  const mod = await import(join(EXTENSION_DIR, "index.ts"));
  assert.equal(typeof mod.default, "function");

  const { pi, handlers, tools } = fakePi();
  await mod.default(pi);
  const notified = [];
  const statuses = [];
  return fn({
    server,
    handlers,
    tools,
    notified,
    statuses,
    ctx: fakeCtx(notified, statuses),
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
      const { default: extension } = await import(join(EXTENSION_DIR, "index.ts"));
      const { pi, handlers } = fakePi();
      await extension(pi);
      await handlers.get("before_agent_start")({ prompt: "hello", systemPrompt: "BASE" }, fakeCtx());
      assert.equal(server.initializes.length, 0);
      await handlers.get("session_shutdown")({}, fakeCtx());
    });
  });
}
