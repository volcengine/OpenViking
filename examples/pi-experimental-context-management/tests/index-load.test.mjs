import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// pi loads extensions through the jiti it bundles, so that is the loader this
// smoke check has to use — a plain `import` of index.ts would not resolve the
// TypeScript or the `.js`-suffixed relative specifiers.
const EXTENSION_DIR = dirname(dirname(fileURLToPath(import.meta.url)));

/**
 * jiti, wherever pi is installed: the global homebrew prefix on this machine,
 * a local node_modules in CI, or an explicit override. Without it these tests
 * skip rather than silently testing nothing.
 */
function findJiti() {
  const candidates = [process.env.PI_JITI_PATH || ""];
  try {
    const require = createRequire(join(EXTENSION_DIR, "package.json"));
    candidates.push(
      require.resolve("@earendil-works/pi-coding-agent/node_modules/jiti/lib/jiti.mjs"),
    );
  } catch {
    // not installed as a dependency here
  }
  candidates.push(
    "/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/node_modules/jiti/lib/jiti.mjs",
    "/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/node_modules/jiti/lib/jiti.mjs",
  );
  return candidates.find((path) => path && existsSync(path)) ?? "";
}

const JITI_PATH = findJiti();

/** The pi surface index.ts touches, and nothing more. */
function fakePi(calls, options = {}) {
  return {
    on(event, handler) {
      calls.events.push(event);
      calls.handlers.set(event, handler);
    },
    registerTool(tool) { calls.tools.push(tool?.name); calls.toolDefs.set(tool?.name, tool); },
    registerCommand(name, def) { calls.commands.push(name); calls.commandDefs.set(name, def); },
    appendEntry(customType, data) { calls.entries.push({ customType, data }); },
    getAllTools() { return options.getAllTools ? options.getAllTools() : []; },
    sendMessage(message, options2) { calls.sent.push({ message, options: options2 }); },
  };
}

/** The `ctx` shape the handlers read: session manager, ui, context usage. */
function fakeCtx(overrides = {}) {
  const branch = overrides.branch ?? [];
  return {
    sessionManager: {
      getSessionId: () => overrides.sessionId ?? "pi-session-abc",
      getBranch: () => branch,
      getLeafId: () => overrides.leafId ?? "entry-9",
    },
    ui: {
      notify: (...args) => (overrides.notified ?? []).push(args),
      setStatus: (...args) => (overrides.statuses ?? []).push(args),
    },
    getContextUsage: () => overrides.usage ?? { tokens: 1000, contextWindow: 262144, percent: 1 },
  };
}

/** Load index.ts and run it against a fake pi, with OV pointed at a dead port. */
async function loadExtension(options = {}) {
  const { createJiti } = await import(JITI_PATH);
  const jiti = createJiti(import.meta.url, { interopDefault: true });
  const mod = await jiti.import(join(EXTENSION_DIR, "index.ts"), { default: true });
  assert.equal(typeof mod, "function");

  const calls = {
    events: [],
    handlers: new Map(),
    tools: [],
    toolDefs: new Map(),
    commands: [],
    commandDefs: new Map(),
    entries: [],
    sent: [],
  };
  await mod(fakePi(calls, options));
  return calls;
}

const OV_ENV = {
  // Port 1 refuses immediately: the health check must fail fast and the
  // extension must still arm its offline half.
  OPENVIKING_URL: "http://127.0.0.1:1",
  OPENVIKING_API_KEY: "test-key",
};

function withDeadServer(t) {
  const previous = {};
  for (const [key, value] of Object.entries(OV_ENV)) {
    previous[key] = process.env[key];
    process.env[key] = value;
  }
  t.after(() => {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  });
}

test("index.ts loads through pi's jiti and registers its surface", { skip: !JITI_PATH }, async (t) => {
  withDeadServer(t);
  const calls = await loadExtension();

  for (const event of [
    "session_start",
    "before_agent_start",
    "context",
    "tool_call",
    "turn_end",
    "session_before_compact",
    "session_compact",
    "session_shutdown",
    "agent_end",
  ]) {
    assert.ok(calls.events.includes(event), `missing handler for ${event}`);
  }
  assert.deepEqual(calls.commands, ["viking"]);
});

test("session_start registers the six viking tools and the three context window tools", { skip: !JITI_PATH }, async (t) => {
  withDeadServer(t);
  const calls = await loadExtension();

  await calls.handlers.get("session_start")({ type: "session_start" }, fakeCtx());
  // session_start is fire-and-forget; before_agent_start awaits the same chain.
  await calls.handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt: "hello", systemPrompt: "BASE" },
    fakeCtx(),
  );

  assert.deepEqual(calls.tools, [
    "viking_search",
    "viking_read",
    "viking_browse",
    "viking_remember",
    "viking_forget",
    "viking_add_resource",
    "new_context",
    "history",
    "get_context_remaining",
  ]);
  // `viking_archive_expand` read viking://session/{sid}, a namespace the server
  // does not have; `history` replaced it and it must not come back.
  assert.equal(calls.tools.includes("viking_archive_expand"), false);
  assert.equal(calls.toolDefs.get("new_context").executionMode, "sequential");
  assert.equal(typeof calls.toolDefs.get("history").execute, "function");
});

test("before_agent_start ships the window guidance and one status line, server or no server", { skip: !JITI_PATH }, async (t) => {
  withDeadServer(t);
  const calls = await loadExtension();

  const result = await calls.handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt: "hello", systemPrompt: "BASE" },
    fakeCtx(),
  );

  assert.ok(result.systemPrompt.startsWith("BASE\n\n"));
  assert.match(result.systemPrompt, /<context-window-management>/);
  assert.match(result.systemPrompt, /new_context, history, get_context_remaining\./);
  // Codex hides this machinery from the user; the demo deliberately does not.
  assert.equal(/never (mention|disclose|reveal)/i.test(result.systemPrompt), false);

  assert.equal(result.message.customType, "ov-context-status");
  assert.equal(result.message.display, true);
  assert.match(result.message.content, /^\[context-status\] window w1/);
});

test("the first status line of a process counts the branch, not an empty window", { skip: !JITI_PATH }, async (t) => {
  withDeadServer(t);
  const calls = await loadExtension();

  // A `pi -c` continuation: the window was opened in the previous process, so
  // the `context` hook — the only place that records the window metrics — has
  // never run here. Reading them off the branch is what stops the first line of
  // the session from claiming an open window has no turns and no idle gap.
  const now = Date.now();
  const headerText =
    '<openviking-context source="context-window">\n' +
    '<context_window id="w2" previous="w1" archive="archive_001">restored</context_window>\n' +
    "</openviking-context>";
  const branch = [
    { type: "message", id: "e1", message: { role: "user", content: "phase one", timestamp: now - 3_600_000 } },
    {
      type: "message",
      id: "e2",
      message: {
        role: "assistant",
        content: [{ type: "toolCall", id: "call-1", name: "new_context", arguments: {} }],
        timestamp: now - 3_500_000,
      },
    },
    {
      type: "custom",
      id: "e3",
      customType: "ov-context-window",
      data: {
        version: 1,
        ovSessionId: "pi-pi-session-abc",
        windowIndex: 2,
        anchorToolCallId: "call-1",
        openedAt: now - 3_400_000,
        headerText,
        reason: "phase one is done",
        notes: "codename ZEPHYR-9942",
        nextSteps: [],
        pendingRequest: "",
        archiveId: "archive_001",
        archiveUri: ARCHIVE,
        taskId: "task-1",
        overviewReady: true,
        overviewUnavailable: false,
        overviewAttempts: 0,
        previousOverview: "",
        siblingToolNames: [],
        syncedEntryCount: 4,
        lastResetAt: now - 3_400_000,
        lastResetBy: "agent",
      },
    },
    {
      type: "message",
      id: "e4",
      message: { role: "toolResult", toolCallId: "call-1", toolName: "new_context", content: "ok", timestamp: now - 3_400_000 },
    },
    // The window's own two turns: a user prompt five minutes ago, answered.
    { type: "message", id: "e5", message: { role: "user", content: "what is left?", timestamp: now - 300_000 } },
    { type: "message", id: "e6", message: { role: "assistant", content: "the tests", timestamp: now - 290_000 } },
  ];

  const first = await calls.handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt: "run them", systemPrompt: "BASE" },
    fakeCtx({ branch }),
  );
  const line = first.message.content.split("\n")[0];
  assert.match(line, /^\[context-status\] window w2 · 1 turn · /, `singular turn count: ${line}`);
  assert.match(line, / 5m since your previous message$/);

  // The window entry is restored once, so a longer branch on the next prompt
  // has to move the count — and pluralize it.
  const grown = [
    ...branch,
    { type: "message", id: "e7", message: { role: "user", content: "and now?", timestamp: now - 120_000 } },
    { type: "message", id: "e8", message: { role: "assistant", content: "done", timestamp: now - 110_000 } },
  ];
  const second = await calls.handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt: "next", systemPrompt: "BASE" },
    fakeCtx({ branch: grown }),
  );
  assert.match(second.message.content, /^\[context-status\] window w2 · 2 turns · /);
  assert.match(second.message.content.split("\n")[0], / 2m since your previous message$/);
});

test("the context hook returns the messages untouched while no window is armed", { skip: !JITI_PATH }, async (t) => {
  withDeadServer(t);
  const calls = await loadExtension();
  await calls.handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt: "hello", systemPrompt: "BASE" },
    fakeCtx(),
  );

  const messages = [
    { role: "user", content: "first", timestamp: 1 },
    { role: "assistant", content: "second", timestamp: 2 },
  ];
  const result = await calls.handlers.get("context")({ type: "context", messages }, fakeCtx());
  assert.deepEqual(result.messages, messages);
});

// ---------------------------------------------------------------------------
// End to end through the wiring: a fake OpenViking server, a real reset, the
// cut the next sampling sees, and the signals that follow it.
// ---------------------------------------------------------------------------

const ROOT = "viking://user/u1/sessions/pi-session-abc";
const ARCHIVE = `${ROOT}/history/archive_001`;

const ARCHIVED_MESSAGES = [
  { id: "m1", role: "user", parts: [{ type: "text", text: "start phase one" }], created_at: "2026-09-10T01:00:00Z" },
  { id: "m2", role: "assistant", parts: [{ type: "text", text: `noted ${"X".repeat(9000)}` }], created_at: "2026-09-10T01:00:05Z" },
]
  .map((row) => JSON.stringify(row))
  .join("\n");

/** The handful of OpenViking routes one reset (and `history`) walks through. */
function startFakeOv(t, options = {}) {
  const seen = [];
  const greps = [];
  const server = createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    const url = new URL(req.url, "http://ov.invalid");
    seen.push(`${req.method} ${url.pathname}`);
    const send = (payload, code = 200) => {
      res.writeHead(code, { "content-type": "application/json" });
      res.end(JSON.stringify(payload));
    };
    if (url.pathname === "/health") return send({ status: "ok" });
    if (/\/messages\/batch$/.test(url.pathname)) return send({ result: { added: 1 } });
    if (/\/messages$/.test(url.pathname)) return send({ result: { ok: true } });
    if (/\/commit$/.test(url.pathname)) {
      return send({
        result: { status: "accepted", archived: 2, task_id: "task-1", archive_uri: ARCHIVE },
      });
    }
    if (req.method === "GET" && /^\/api\/v1\/sessions\/[^/]+$/.test(url.pathname)) {
      return send({ result: { uri: ROOT } });
    }
    if (url.pathname === "/api/v1/fs/ls") {
      // The gateway blocks this route on some deployments (403 ApiBlocked).
      if (options.blockLs) return send({ error: { code: "ApiBlocked", message: "blocked" } }, 403);
      if (options.emptyLs) return send({ result: [] });
      return send({
        result: [{ uri: ARCHIVE, isDir: true, modTime: "2026-09-10T01:00:00Z", abstract: "# Working Memory\nphase one" }],
      });
    }
    if (url.pathname === "/api/v1/content/read") {
      const uri = url.searchParams.get("uri") || "";
      if (uri.endsWith(".overview.md")) return send({ result: "# Working Memory\n\nphase one" });
      if (uri.endsWith("messages.jsonl")) return send({ result: ARCHIVED_MESSAGES });
      return send({ error: { message: "NOT_FOUND" } }, 404);
    }
    if (url.pathname === "/api/v1/search/grep") {
      greps.push(JSON.parse(body || "{}"));
      return send({
        result: {
          matches: [
            { uri: `${ARCHIVE}/messages.jsonl`, line: 2, content: "Z".repeat(900) },
            { uri: `${ARCHIVE}/.overview.md`, line: 4, content: "codename ZEPHYR-9942" },
          ],
        },
      });
    }
    // Recall: a block the injector would prepend to the newest user message.
    if (url.pathname === "/api/v1/search/recall") {
      return send({ result: { rendered: "- recalled memory", entries: [{ uri: "viking://x" }] } });
    }
    return send({ error: { message: "not found" } }, 404);
  });
  t.after(() => server.close());
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve({ port: server.address().port, seen, greps }));
  });
}

/** Isolated env: a live fake server and a pending queue that is not the user's. */
function withFakeServer(t, port) {
  const dir = mkdtempSync(join(tmpdir(), "ov-index-"));
  const previous = {};
  const env = {
    OPENVIKING_URL: `http://127.0.0.1:${port}`,
    OPENVIKING_API_KEY: "test-key",
    OPENVIKING_PENDING_DIR: dir,
  };
  for (const [key, value] of Object.entries(env)) {
    previous[key] = process.env[key];
    process.env[key] = value;
  }
  t.after(() => {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    rmSync(dir, { recursive: true, force: true });
  });
}

test("a reset archives the window, cuts it out of the next context and stays quiet in the same turn", { skip: !JITI_PATH }, async (t) => {
  const { port } = await startFakeOv(t);
  withFakeServer(t, port);
  const calls = await loadExtension();

  const branch = [
    { type: "message", id: "e1", message: { role: "user", content: "start phase one PAD1", timestamp: 1 } },
    { type: "message", id: "e2", message: { role: "assistant", content: "phase one done", timestamp: 2 } },
    { type: "message", id: "e3", message: { role: "user", content: "wrap it up", timestamp: 3 } },
    {
      type: "message",
      id: "e4",
      message: {
        role: "assistant",
        content: [{ type: "toolCall", id: "call-1", name: "new_context", arguments: {} }],
        timestamp: 4,
      },
    },
  ];
  // Pi reports the *untransformed* session, so right after the cut this number
  // still describes the window that was just archived.
  const usage = { tokens: 250000, contextWindow: 262144, percent: 95 };
  const ctx = fakeCtx({ branch, usage });

  await calls.handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt: "wrap it up", systemPrompt: "BASE" },
    ctx,
  );

  const result = await calls.toolDefs.get("new_context").execute(
    "call-1",
    { reason: "phase one is done", notes: "codename ZEPHYR-9942", next_steps: ["start phase two"] },
    undefined,
    () => {},
    ctx,
  );
  assert.equal(result.isError, false);
  // A reset must never terminate the agent loop: the next sampling is what
  // consumes it.
  assert.equal("terminate" in result, false);
  assert.match(result.content[0].text, /Context window w2 is open/);

  // The turn the reset happened in ends here. Its assistant message predates
  // the reset, so no reminder may fire off the stale usage — one would tell the
  // model to reset again immediately and burn the level for the whole window.
  await calls.handlers.get("turn_end")(
    {
      type: "turn_end",
      turnIndex: 0,
      message: branch[3].message,
      toolResults: [{ role: "toolResult", toolCallId: "call-1" }],
    },
    ctx,
  );
  assert.deepEqual(calls.sent, [], "no reminder in the turn that reset the window");

  // The next sampling: the archived window is gone, the header is the context.
  const messages = [
    { role: "user", content: "start phase one PAD1", timestamp: 1 },
    { role: "assistant", content: "phase one done", timestamp: 2 },
    { role: "user", content: "wrap it up", timestamp: 3 },
    {
      role: "assistant",
      content: [{ type: "toolCall", id: "call-1", name: "new_context", arguments: {} }],
      timestamp: 4,
    },
    { role: "toolResult", toolCallId: "call-1", toolName: "new_context", content: "ok", timestamp: 5 },
  ];
  const cut = await calls.handlers.get("context")({ type: "context", messages }, ctx);
  assert.equal(cut.messages.length, 1);
  assert.equal(cut.messages[0].role, "user");
  const header = String(cut.messages[0].content);
  assert.ok(header.startsWith('<openviking-context source="context-window">'), header.slice(0, 80));
  assert.match(header, /id="w2" previous="w1" archive="archive_001"/);
  assert.match(header, /ZEPHYR-9942/);
  assert.match(header, /<working-memory[^>]*>/); // the fake's Working Memory
  assert.match(header, /<pending-request>\s*wrap it up/);
  // The archived turns are gone; only the notes and the pending request survive.
  assert.equal(header.includes("PAD1"), false);
  // Recall runs after the cut and its guard leaves the frozen header alone.
  assert.equal(header.includes("recalled memory"), false);

  // One window entry, so `pi -c` can rebuild the same anchor and header.
  const windowEntries = calls.entries.filter((e) => e.customType === "ov-context-window");
  assert.equal(windowEntries.length, 1);
  assert.equal(windowEntries[0].data.archiveId, "archive_001");
  assert.equal(windowEntries[0].data.anchorToolCallId, "call-1");
  assert.equal(windowEntries[0].data.overviewReady, true);

  // A later turn, whose assistant answered inside the new window: now the
  // reported usage is about this window again, so a full window does warn —
  // once, and steered because the batch is still running.
  await calls.handlers.get("turn_end")(
    {
      type: "turn_end",
      turnIndex: 1,
      message: { role: "assistant", content: "working", timestamp: Date.now() + 1000 },
      toolResults: [{ role: "toolResult", toolCallId: "call-2" }],
    },
    ctx,
  );
  assert.equal(calls.sent.length, 1);
  assert.equal(calls.sent[0].message.customType, "ov-context-reminder");
  assert.equal(calls.sent[0].options.deliverAs, "steer");
  assert.match(calls.sent[0].message.content, /new_context/);

  // At the end of a turn with no tool results the reminder waits for the next
  // turn instead of jumping the queue — and it never repeats a level.
  await calls.handlers.get("turn_end")(
    {
      type: "turn_end",
      turnIndex: 2,
      message: { role: "assistant", content: "still working", timestamp: Date.now() + 2000 },
      toolResults: [],
    },
    ctx,
  );
  assert.equal(calls.sent.length, 1, "each level fires once per window");
});

test("history reads the archived window back and get_context_remaining reports the new one", { skip: !JITI_PATH }, async (t) => {
  const { port, greps } = await startFakeOv(t);
  withFakeServer(t, port);
  const calls = await loadExtension();

  const branch = [
    { type: "message", id: "e1", message: { role: "user", content: "start phase one", timestamp: 1 } },
    {
      type: "message",
      id: "e2",
      message: {
        role: "assistant",
        content: [{ type: "toolCall", id: "call-1", name: "new_context", arguments: {} }],
        timestamp: 2,
      },
    },
  ];
  const ctx = fakeCtx({ branch, usage: { tokens: 40000, contextWindow: 262144, percent: 15 } });
  await calls.handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt: "start phase one", systemPrompt: "BASE" },
    ctx,
  );
  await calls.toolDefs.get("new_context").execute(
    "call-1",
    { reason: "phase one is done", notes: "codename ZEPHYR-9942" },
    undefined,
    () => {},
    ctx,
  );

  const history = calls.toolDefs.get("history");
  const run = async (params) => (await history.execute("h", params, undefined, () => {}, ctx)).content[0].text;

  // w1 is the oldest archive; the open window is listed but not readable.
  const windows = await run({ action: "list_windows" });
  assert.match(windows, /^w1 {2}archive_001 {2}2026-09-10T01:00:00Z {2}# Working Memory$/m);
  assert.match(windows, /^w2 {2}\(current\) {2}this window is still open and not archived$/m);

  const items = await run({ action: "list_items", window: "w1" });
  assert.match(items, /\[id: w1:0\] {2}user 2026-09-10T01:00:00Z {2}start phase one/);
  assert.match(items, /\[id: w1:1\].*\[truncated, \d+ more characters\]/);
  assert.match(items, /Showing 0-1 of 2 items \(end of window\)\./);

  // read_item goes up to historyItemMaxChars (8000) rather than the list clip.
  const item = await run({ action: "read_item", item: "w1:1" });
  assert.match(item, /^\[id: w1:1\] {2}assistant {2}2026-09-10T01:00:05Z$/m);
  assert.ok(item.length > 8000 && item.length < 8200, `unexpected length ${item.length}`);
  assert.match(await run({ action: "read_item", item: "w1:7" }), /has 2 items \(0-1\); 7 is out of range/);
  assert.match(await run({ action: "list_items", window: "w7" }), /No archived window named "w7"/);

  const found = await run({ action: "search_contents", query: "ZEPHYR" });
  assert.match(found, /^w1 {2}archive_001$/m);
  // Long grep lines are clipped, and a messages.jsonl hit points at read_item.
  assert.match(found, /messages\.jsonl:2 {2}Z+… \[truncated, \d+ more characters\] {2}→ \{"action":"read_item","item":"w1:1"\}/);
  assert.match(found, /\.overview\.md:4 {2}codename ZEPHYR-9942/);
  assert.equal(greps.at(-1).case_insensitive, true);

  // A query that is not a valid expression is searched verbatim instead of
  // failing server-side as a broken regex.
  await run({ action: "search_contents", query: "phase one (unfinished" });
  assert.equal(greps.at(-1).pattern, "phase one \\(unfinished");

  const remaining = (await calls.toolDefs
    .get("get_context_remaining")
    .execute("g", {}, undefined, () => {}, ctx)).content[0].text;
  assert.match(remaining, /^window: w2, opened /m);
  assert.match(remaining, /^archives: 1 \(latest archive_001, Working Memory ready\)$/m);
  assert.match(remaining, /^tokens_left: /m);
  assert.match(remaining, /^advice: /m);
});

test("a window restored with OpenViking down still cuts the archived conversation", { skip: !JITI_PATH }, async (t) => {
  withDeadServer(t);
  const calls = await loadExtension();

  const headerText =
    '<openviking-context source="context-window">\n' +
    '<context_window id="w2" previous="w1" archive="archive_001">restored</context_window>\n' +
    "</openviking-context>";
  const branch = [
    { type: "message", id: "e1", message: { role: "user", content: "phase one PAD1", timestamp: 1 } },
    {
      type: "message",
      id: "e2",
      message: {
        role: "assistant",
        content: [{ type: "toolCall", id: "call-1", name: "new_context", arguments: {} }],
        timestamp: 2,
      },
    },
    {
      type: "custom",
      customType: "ov-context-window",
      data: {
        version: 1,
        ovSessionId: "pi-pi-session-abc",
        windowIndex: 2,
        anchorToolCallId: "call-1",
        openedAt: 1000,
        headerText,
        reason: "phase one is done",
        notes: "codename ZEPHYR-9942",
        nextSteps: [],
        pendingRequest: "",
        archiveId: "archive_001",
        archiveUri: ARCHIVE,
        taskId: "task-1",
        overviewReady: true,
        overviewUnavailable: false,
        overviewAttempts: 0,
        previousOverview: "",
        siblingToolNames: [],
        syncedEntryCount: 42,
        lastResetAt: 1000,
        lastResetBy: "agent",
      },
    },
  ];
  const ctx = fakeCtx({ branch });

  const started = await calls.handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt: "what was the codename?", systemPrompt: "BASE" },
    ctx,
  );
  // Window state and tools are restored before the health check, so an
  // unreachable server does not hand the archived window back to the model.
  assert.match(started.message.content, /^\[context-status\] window w2/);
  assert.ok(calls.tools.includes("new_context"));

  const messages = [
    { role: "user", content: "phase one PAD1", timestamp: 1 },
    {
      role: "assistant",
      content: [{ type: "toolCall", id: "call-1", name: "new_context", arguments: {} }],
      timestamp: 2,
    },
    { role: "toolResult", toolCallId: "call-1", toolName: "new_context", content: "ok", timestamp: 3 },
    { role: "user", content: "what was the codename?", timestamp: 4 },
  ];
  const cut = await calls.handlers.get("context")({ type: "context", messages }, ctx);
  assert.equal(cut.messages.length, 1);
  assert.ok(String(cut.messages[0].content).startsWith(headerText));
  // The window header merges into the surviving user message instead of
  // producing two user messages in a row.
  assert.match(String(cut.messages[0].content), /what was the codename\?$/);
  assert.equal(JSON.stringify(cut.messages).includes("PAD1"), false);

  // Offline, pi keeps its own summarizer: no archive, no header as summary.
  const compact = await calls.handlers.get("session_before_compact")(
    { type: "session_before_compact", preparation: { firstKeptEntryId: "e2", tokensBefore: 100 }, branchEntries: branch },
    ctx,
  );
  assert.equal(compact, undefined);

  // A compaction that was not ours already cut the history natively, so the
  // virtual boundary has to be released or the model would lose live context.
  await calls.handlers.get("session_compact")(
    { type: "session_compact", fromExtension: false, compactionEntry: {}, reason: "threshold" },
    ctx,
  );
  const after = await calls.handlers.get("context")({ type: "context", messages }, ctx);
  assert.equal(after.messages.length, messages.length);

  // The final watermark is persisted even though the server never answered.
  await calls.handlers.get("session_shutdown")({ type: "session_shutdown", reason: "quit" }, ctx);
  const windowEntries = calls.entries.filter((e) => e.customType === "ov-context-window");
  assert.ok(windowEntries.length >= 1);
  assert.equal(windowEntries.at(-1).data.windowIndex, 3, "the absorbed compaction opened w3");
});

test("the fork ships no takeover module", () => {
  for (const file of ["takeover.ts", "lib/takeover-core.mjs", "shared/recall-ledger.mjs"]) {
    assert.equal(existsSync(join(EXTENSION_DIR, file)), false, `${file} should not exist`);
  }
});

// ---------------------------------------------------------------------------
// Coexistence with the non-experimental extension, and the signals that must
// not fire off a stale usage reading.
// ---------------------------------------------------------------------------

/** A `viking_search` some other extension registered. */
const PEER_TOOL = { name: "viking_search", sourceInfo: { path: "/somewhere/else/openviking" } };

test("a peer that registers viking_search later still makes this extension stand down", { skip: !JITI_PATH }, async (t) => {
  withDeadServer(t);
  // The other extension registers only after its own awaited health check, so
  // at the moment start() begins the probe sees nothing.
  let peerLoaded = false;
  const calls = await loadExtension({ getAllTools: () => (peerLoaded ? [PEER_TOOL] : []) });

  const notified = [];
  await calls.handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt: "hello", systemPrompt: "BASE" },
    fakeCtx({ notified }),
  );
  assert.ok(calls.tools.includes("viking_search"), "the early probe found nothing, so we registered");

  peerLoaded = true;
  const result = await calls.handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt: "again", systemPrompt: "BASE" },
    fakeCtx({ notified }),
  );
  assert.equal(result, undefined, "the second writer stands down instead of syncing the same session");
  assert.ok(
    notified.some(([message]) => /another OpenViking extension is already active/.test(message)),
    JSON.stringify(notified),
  );

  // And it stays down: no cut, no sync, no reminders.
  const messages = [{ role: "user", content: "hi", timestamp: 1 }];
  assert.equal(await calls.handlers.get("context")({ type: "context", messages }, fakeCtx()), undefined);
});

test("our own viking_search is not mistaken for a peer's", { skip: !JITI_PATH }, async (t) => {
  withDeadServer(t);
  const calls = await loadExtension({
    getAllTools: () => [{ name: "viking_search", sourceInfo: { path: EXTENSION_DIR } }],
  });
  const result = await calls.handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt: "hello", systemPrompt: "BASE" },
    fakeCtx(),
  );
  assert.ok(result?.systemPrompt, "the extension keeps working");
  assert.ok(calls.tools.includes("new_context"));
});

test("a turn that ended in a provider error does not re-arm the reminders", { skip: !JITI_PATH }, async (t) => {
  const { port } = await startFakeOv(t);
  withFakeServer(t, port);
  const calls = await loadExtension();

  const branch = [
    { type: "message", id: "e1", message: { role: "user", content: "start", timestamp: 1 } },
    {
      type: "message",
      id: "e2",
      message: {
        role: "assistant",
        content: [{ type: "toolCall", id: "call-1", name: "new_context", arguments: {} }],
        timestamp: 2,
      },
    },
  ];
  // pi reports the untransformed session, so this is the *closed* window's size.
  const ctx = fakeCtx({ branch, usage: { tokens: 250000, contextWindow: 262144, percent: 95 } });
  await calls.handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt: "start", systemPrompt: "BASE" },
    ctx,
  );
  await calls.toolDefs.get("new_context").execute(
    "call-1", { reason: "phase one done", notes: "codename ZEPHYR-9942" }, undefined, () => {}, ctx,
  );

  // A provider error (or Esc) ends the turn with a freshly stamped assistant
  // message pi refuses to read usage from. Re-arming on it would fire the hard
  // reminder into a window that holds nothing but the header.
  for (const stopReason of ["error", "aborted"]) {
    await calls.handlers.get("turn_end")(
      {
        type: "turn_end",
        turnIndex: 1,
        message: { role: "assistant", content: "", stopReason, timestamp: Date.now() + 5000 },
        toolResults: [],
      },
      ctx,
    );
  }
  assert.deepEqual(calls.sent, [], "no reminder off a response pi does not trust");

  // A real response in the new window re-arms as before.
  await calls.handlers.get("turn_end")(
    {
      type: "turn_end",
      turnIndex: 2,
      message: { role: "assistant", content: "working", stopReason: "stop", timestamp: Date.now() + 6000 },
      toolResults: [],
    },
    ctx,
  );
  assert.equal(calls.sent.length, 1);
  assert.equal(calls.sent[0].message.customType, "ov-context-reminder");
});

test("a recall failure costs the recall block, never the window cut", { skip: !JITI_PATH }, async (t) => {
  const { port } = await startFakeOv(t);
  withFakeServer(t, port);
  const calls = await loadExtension();

  const branch = [
    { type: "message", id: "e1", message: { role: "user", content: "start PAD1", timestamp: 1 } },
    {
      type: "message",
      id: "e2",
      message: {
        role: "assistant",
        content: [{ type: "toolCall", id: "call-1", name: "new_context", arguments: {} }],
        timestamp: 2,
      },
    },
  ];
  const ctx = fakeCtx({ branch });
  await calls.handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt: "start PAD1", systemPrompt: "BASE" },
    ctx,
  );
  await calls.toolDefs.get("new_context").execute(
    "call-1", { reason: "done", notes: "codename ZEPHYR-9942" }, undefined, () => {}, ctx,
  );

  // pi keeps the untransformed list when a context handler throws, so a recall
  // exception must not be allowed to escape: it would hand the model the
  // archived window back together with the tool result that archived it.
  const messages = [
    { role: "user", content: "start PAD1", timestamp: 1 },
    {
      role: "assistant",
      content: [{ type: "toolCall", id: "call-1", name: "new_context", arguments: {} }],
      timestamp: 2,
    },
    { role: "toolResult", toolCallId: "call-1", toolName: "new_context", content: "ok", timestamp: 3 },
    // A message shape injectRecall would trip over.
    { role: "user", content: null, timestamp: 4 },
    null,
  ];
  const cut = await calls.handlers.get("context")({ type: "context", messages }, ctx);
  assert.ok(Array.isArray(cut.messages));
  assert.ok(String(cut.messages[0].content).includes("ZEPHYR-9942"));
  assert.ok(cut.messages.length < messages.length, "the archived window stays cut");
  assert.equal(
    cut.messages.some((m) => m?.role === "toolResult" || m?.role === "assistant"),
    false,
    "the reset's own call and result are gone",
  );
  assert.equal(JSON.stringify(cut.messages).includes("recalled memory"), false, "recall failed, the cut did not");
});

test("history says so when OpenViking cannot answer the archive listing", { skip: !JITI_PATH }, async (t) => {
  const { port } = await startFakeOv(t, { blockLs: true });
  withFakeServer(t, port);
  const calls = await loadExtension();
  const ctx = fakeCtx();
  await calls.handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt: "hi", systemPrompt: "BASE" },
    ctx,
  );

  const out = await calls.toolDefs
    .get("history")
    .execute("h", { action: "list_windows" }, undefined, () => {}, ctx);
  const body = out.content[0].text;
  assert.match(body, /did not answer the archive listing/);
  assert.equal(/0 archived windows/.test(body), false, "an unreadable history is not an empty one");
});

test("an empty history does not point list_items at the open window", { skip: !JITI_PATH }, async (t) => {
  const { port } = await startFakeOv(t, { emptyLs: true });
  withFakeServer(t, port);
  const calls = await loadExtension();
  const ctx = fakeCtx();
  await calls.handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt: "hi", systemPrompt: "BASE" },
    ctx,
  );

  const body = (await calls.toolDefs
    .get("history")
    .execute("h", { action: "list_windows" }, undefined, () => {}, ctx)).content[0].text;
  assert.match(body, /^0 archived windows in this session:$/m);
  assert.match(body, /^w1 {2}\(current\)/m);
  // The only id in the list is the open window, and list_items refuses it — a
  // pointer at it would buy a refused call and imply history can read it.
  assert.equal(
    /\{"action":"list_items","window":"w1"\}/.test(body),
    false,
    "no pointer at the window that is not archived",
  );
  assert.match(body, /Nothing is archived yet/);
});

test("session_before_compact runs offline so the recent-reset guard still applies", { skip: !JITI_PATH }, async (t) => {
  const { port } = await startFakeOv(t);
  withFakeServer(t, port);
  const calls = await loadExtension();

  const branch = [
    { type: "message", id: "e1", message: { role: "user", content: "start", timestamp: 1 } },
    {
      type: "message",
      id: "e2",
      message: {
        role: "assistant",
        content: [{ type: "toolCall", id: "call-1", name: "new_context", arguments: {} }],
        timestamp: 2,
      },
    },
  ];
  const ctx = fakeCtx({ branch });
  await calls.handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt: "start", systemPrompt: "BASE" },
    ctx,
  );
  await calls.toolDefs.get("new_context").execute(
    "call-1", { reason: "done", notes: "codename ZEPHYR-9942" }, undefined, () => {}, ctx,
  );

  // A provider hiccup right after the reset makes pi estimate from the
  // untransformed list and try to compact a window that just opened. The guard
  // needs no network, so it has to run even with the server gone.
  const compact = await calls.handlers.get("session_before_compact")(
    { type: "session_before_compact", preparation: { firstKeptEntryId: "e1", tokensBefore: 10 }, branchEntries: branch },
    ctx,
  );
  assert.ok(compact?.compaction, "the freshly opened window is handed back as the summary");
  assert.equal(compact.compaction.details.reason, "recent-reset-guard");
  assert.ok(compact.compaction.summary.includes("ZEPHYR-9942"));
});

test("window ids survive a compaction that produced no archive", { skip: !JITI_PATH }, async (t) => {
  const { port } = await startFakeOv(t);
  withFakeServer(t, port);
  const calls = await loadExtension();

  const branch = [
    { type: "message", id: "e1", message: { role: "user", content: "start", timestamp: 1 } },
    {
      type: "message",
      id: "e2",
      message: {
        role: "assistant",
        content: [{ type: "toolCall", id: "call-1", name: "new_context", arguments: {} }],
        timestamp: 2,
      },
    },
  ];
  const ctx = fakeCtx({ branch });
  await calls.handlers.get("before_agent_start")(
    { type: "before_agent_start", prompt: "start", systemPrompt: "BASE" },
    ctx,
  );
  await calls.toolDefs.get("new_context").execute(
    "call-1", { reason: "done", notes: "codename ZEPHYR-9942" }, undefined, () => {}, ctx,
  );
  // w2 closes without an archive: pi compacted on its own.
  await calls.handlers.get("session_compact")(
    { type: "session_compact", fromExtension: false, compactionEntry: {}, reason: "threshold" },
    ctx,
  );

  const windows = (await calls.toolDefs
    .get("history")
    .execute("h", { action: "list_windows" }, undefined, () => {}, ctx)).content[0].text;
  // archive_001 keeps the id of the window it closed, and the open window is w3
  // — numbering the server's list positionally would call the open one w2 and
  // point every recovery hint at a window history cannot resolve.
  assert.match(windows, /^w1 {2}archive_001 {2}/m);
  assert.match(windows, /^w3 {2}\(current\)/m);
  assert.match(
    (await calls.toolDefs.get("history").execute("h", { action: "list_items", window: "w1" }, undefined, () => {}, ctx))
      .content[0].text,
    /^w1 \(archive_001\), 2 items:/m,
  );

  const remaining = (await calls.toolDefs
    .get("get_context_remaining")
    .execute("g", {}, undefined, () => {}, ctx)).content[0].text;
  assert.match(remaining, /^window: w3/m);
  assert.match(remaining, /^archives: 1 \(latest archive_001/m, "one archive, not two windows worth");
});
