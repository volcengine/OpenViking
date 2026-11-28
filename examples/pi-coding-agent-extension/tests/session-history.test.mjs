/** How the extension applies the flow's session_history default and session-history note. */

import test, { after } from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { createServer } from "node:http";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

registerHooks({
  resolve(specifier, context, next) {
    try { return next(specifier, context); } catch (error) {
      if (!specifier.startsWith(".") || !specifier.endsWith(".js")) throw error;
      return next(specifier.slice(0, -3) + ".ts", context);
    }
  },
});

let toolNames;
const calls = [];
const server = createServer(async (req, res) => {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  res.setHeader("content-type", "application/json");
  if (!req.url.startsWith("/mcp")) return res.end(JSON.stringify({ status: "ok", result: {} }));
  if (req.method !== "POST") return res.writeHead(405).end();
  const message = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (message.id === undefined) return res.writeHead(202).end();
  if (message.method === "tools/call") calls.push(message.params);
  const result = message.method === "initialize"
    ? { protocolVersion: message.params.protocolVersion, capabilities: { tools: {} }, serverInfo: { name: "fake", version: "1" } }
    : message.method === "tools/list"
      ? { tools: toolNames.map((name) => ({ name, inputSchema: { type: "object" } })) }
      : { content: [] };
  res.end(JSON.stringify({ jsonrpc: "2.0", id: message.id, result }));
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const dir = mkdtempSync(join(tmpdir(), "ov-pi-history-"));
writeFileSync(join(dir, "ovcli.conf"), JSON.stringify({ plugin: { pi: {} } }));
for (const name of Object.keys(process.env)) if (/^(OV_|OPENVIKING_)/.test(name)) delete process.env[name];
Object.assign(process.env, {
  OPENVIKING_CREDENTIAL_SOURCE: "env",
  OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
  OPENVIKING_API_KEY: "pi-history-key",
  OPENVIKING_CLI_CONFIG_FILE: join(dir, "ovcli.conf"),
  OPENVIKING_CONFIG_FILE: join(dir, "ov.conf"),
  OPENVIKING_PENDING_DIR: join(dir, "pending"),
});
const { default: extension } = await import("../index.ts");

let shutdown = null;
after(async () => {
  await shutdown?.();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
  rmSync(dir, { recursive: true, force: true });
});

let boots = 0;
async function boot(branch = [], names = ["find", "session_history"], header = {}) {
  await shutdown?.();
  process.env.OPENVIKING_HOOK_STATE_DIR = join(dir, `state-${boots++}`);
  toolNames = names;
  const handlers = new Map();
  const tools = new Map();
  await extension({
    on: (event, handler) => handlers.set(event, handler),
    registerTool: (tool) => tools.set(tool.name, tool),
    registerCommand() {}, appendEntry() {}, getAllTools: () => [], sendMessage() {},
  });
  const ctx = {
    sessionManager: { getSessionId: () => "history-session", getBranch: () => branch, getLeafId: () => branch.at(-1)?.id ?? null, getHeader: () => header },
    ui: { notify() {}, setStatus() {} },
    getContextUsage: () => ({ tokens: 0, contextWindow: 262144, percent: 0 }),
  };
  shutdown = () => handlers.get("session_shutdown")({ type: "session_shutdown", reason: "quit" }, ctx);
  return {
    tools,
    toolCall: (event) => handlers.get("tool_call")(event, ctx),
    turn: async () => (await handlers.get("before_agent_start")(
      { type: "before_agent_start", prompt: "hello", systemPrompt: "BASE" }, ctx))?.systemPrompt ?? "BASE",
    compact: () => handlers.get("session_compact")({ type: "session_compact", reason: "manual" }, ctx),
  };
}

const earlier = [{ type: "message", id: "e1", message: { role: "user", content: "earlier", timestamp: 1 } }];

test("session_history defaults to this session's OV id", async () => {
  const { toolCall, turn } = await boot();
  await turn();
  const event = { toolName: "openviking_session_history", input: { action: "overview" } };
  assert.equal(await toolCall(event), undefined);
  assert.equal(event.input.session, "pi-history-session");
});

test("the note follows a compaction and a resume, not a fork", async () => {
  const fresh = await boot();
  assert.doesNotMatch(await fresh.turn(), /session-history/);
  await fresh.compact();
  assert.match(await fresh.turn(), /what the compaction summary replaced/);
  assert.match(await (await boot(earlier)).turn(), /your context no longer holds/);
  assert.doesNotMatch(await (await boot(earlier, undefined, { parentSession: "parent" })).turn(), /session-history/);
});
