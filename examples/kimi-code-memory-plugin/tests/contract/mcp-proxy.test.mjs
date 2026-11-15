/**
 * The MCP proxy as the host runs it.
 *
 * The host spawns this process, writes JSON-RPC to its stdin and reads JSON-RPC
 * from its stdout. So the assertions are about that pipe: a clean handshake, one
 * answer per request id, no log line ever appearing on stdout, and the tool
 * catalogue coming from the server rather than from this plugin.
 */

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { fakeOvEnv, startFakeOv } from "../fake-ov/server.mjs";
import { withKimiHome } from "../fixtures/kimi-home.mjs";

const PROXY = fileURLToPath(new URL("../../servers/mcp-proxy.mjs", import.meta.url));

/**
 * A fake OpenViking MCP endpoint.
 *
 * The shared proxy speaks streamable HTTP to `/mcp`; the answers here are the
 * minimum a client needs to complete a handshake and list tools.
 */
function mcpServer() {
  return async (request) => {
    if (request.path === "/health") return { body: { status: "ok" } };
    if (request.path !== "/mcp") return undefined;
    const body = request.body;
    if (!body || typeof body !== "object") return undefined;
    if (body.method === "initialize") {
      return {
        body: {
          jsonrpc: "2.0",
          id: body.id,
          result: {
            protocolVersion: "2025-06-18",
            capabilities: { tools: {} },
            serverInfo: { name: "openviking", version: "1.5.0" },
          },
        },
      };
    }
    if (body.method === "tools/list") {
      return {
        body: {
          jsonrpc: "2.0",
          id: body.id,
          result: {
            tools: [
              { name: "read", description: "Read a viking:// URI", inputSchema: { type: "object" } },
              { name: "find", description: "Semantic search", inputSchema: { type: "object" } },
              { name: "remember", description: "Store a memory", inputSchema: { type: "object" } },
            ],
          },
        },
      };
    }
    if (body.method === "tools/call") {
      return {
        body: {
          jsonrpc: "2.0",
          id: body.id,
          result: { content: [{ type: "text", text: "called" }] },
        },
      };
    }
    if (body.id === undefined) return { body: "" };
    return { body: { jsonrpc: "2.0", id: body.id, result: {} } };
  };
}

/** Run the proxy, send these messages, collect its stdout lines. */
async function runProxy({ env, messages, expectLines = 1, timeoutMs = 15_000 }) {
  return await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [PROXY], {
      env: { ...process.env, ...env },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    const out = [];
    const err = [];
    const lines = [];
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.kill();
      resolve({ lines, stdout: Buffer.concat(out).toString("utf8"), stderr: Buffer.concat(err).toString("utf8") });
    };
    const timer = setTimeout(finish, timeoutMs);
    child.stdout.on("data", (chunk) => {
      out.push(chunk);
      const text = Buffer.concat(out).toString("utf8");
      const parts = text.split("\n").filter(Boolean);
      while (lines.length < parts.length) {
        const line = parts[lines.length];
        try {
          lines.push(JSON.parse(line));
        } catch {
          // A non-JSON line on stdout is itself the failure; keep it as a
          // marker so the assertion reports it.
          lines.push({ __nonJson: line });
        }
      }
      if (lines.length >= expectLines) finish();
    });
    child.stderr.on("data", (chunk) => err.push(chunk));
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", () => {
      clearTimeout(timer);
      if (!settled) {
        settled = true;
        resolve({ lines, stdout: Buffer.concat(out).toString("utf8"), stderr: Buffer.concat(err).toString("utf8") });
      }
    });
    for (const message of messages) child.stdin.write(`${JSON.stringify(message)}\n`);
  });
}

test("the proxy completes a handshake and answers with the server's tool catalogue", async () => {
  const server = await startFakeOv(mcpServer());
  try {
    await withKimiHome(async ({ home }) => {
      const result = await runProxy({
        env: { ...fakeOvEnv(server), KIMI_CODE_HOME: home, OPENVIKING_DEBUG: "0" },
        messages: [
          {
            jsonrpc: "2.0",
            id: 1,
            method: "initialize",
            params: {
              protocolVersion: "2025-06-18",
              capabilities: {},
              clientInfo: { name: "kimi-code", version: "2.6.2" },
            },
          },
        ],
      });

      assert.equal(result.lines.length, 1, result.stdout + result.stderr);
      const [reply] = result.lines;
      assert.equal(reply.__nonJson, undefined, `stdout was polluted: ${reply.__nonJson}`);
      assert.equal(reply.id, 1);
      assert.equal(reply.result.serverInfo.name, "openviking");
      // The version is the server's, not a constant in this plugin.
      assert.equal(reply.result.serverInfo.version, "1.5.0");
    });
  } finally {
    await server.close();
  }
});

test("tools/list comes from the server, with no local schema copy", async () => {
  const server = await startFakeOv(mcpServer());
  try {
    await withKimiHome(async ({ home }) => {
      const result = await runProxy({
        env: { ...fakeOvEnv(server), KIMI_CODE_HOME: home },
        messages: [
          { jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "kimi-code", version: "2.6.2" } } },
          { jsonrpc: "2.0", id: 2, method: "tools/list" },
        ],
        expectLines: 2,
      });
      const list = result.lines.find((line) => line.id === 2);
      assert.ok(list, result.stdout + result.stderr);
      assert.deepEqual(list.result.tools.map((tool) => tool.name), ["read", "find", "remember"]);
    });
  } finally {
    await server.close();
  }
});

test("a notification gets no ordinary response, and concurrent ids keep their answers", async () => {
  const server = await startFakeOv(mcpServer());
  try {
    await withKimiHome(async ({ home }) => {
      const result = await runProxy({
        env: { ...fakeOvEnv(server), KIMI_CODE_HOME: home },
        messages: [
          { jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "kimi-code", version: "2.6.2" } } },
          { jsonrpc: "2.0", method: "notifications/initialized" },
          { jsonrpc: "2.0", id: 10, method: "tools/list" },
          { jsonrpc: "2.0", id: 11, method: "tools/call", params: { name: "read", arguments: { uri: "viking://a" } } },
        ],
        expectLines: 3,
      });
      const ids = result.lines.map((line) => line.id).sort();
      assert.deepEqual(ids, [1, 10, 11], result.stdout);
      // The notification produced no id of its own.
      assert.equal(result.lines.some((line) => line.id === undefined), false);
    });
  } finally {
    await server.close();
  }
});

test("stdout carries only JSON-RPC, even when the server is unreachable", async () => {
  await withKimiHome(async ({ home }) => {
    // A port nothing listens on: the proxy must answer with a JSON-RPC error,
    // not with a log line on stdout.
    const result = await runProxy({
      env: {
        OPENVIKING_URL: "http://127.0.0.1:9",
        OPENVIKING_API_KEY: "sk-test",
        OPENVIKING_AUTH_MODE: "api_key",
        OPENVIKING_CREDENTIAL_SOURCE: "env",
        OPENVIKING_DEBUG: "1",
        KIMI_CODE_HOME: home,
      },
      messages: [{ jsonrpc: "2.0", id: 7, method: "tools/list" }],
      timeoutMs: 8_000,
    });
    for (const line of result.lines) {
      assert.equal(line.__nonJson, undefined, `stdout pollution: ${line.__nonJson}`);
    }
    // Whatever it answered, the id is accounted for and the process is clean.
    // An unreachable server must answer the request it was given, not go silent.
    assert.equal(result.lines.length, 1, result.stdout + result.stderr);
    assert.equal(result.lines[0].id, 7);
    assert.ok(result.lines[0].error, "an unreachable server must answer with a JSON-RPC error");
  });
});

test("the proxy resolves the same connection the hooks do", async () => {
  const { readProxyConfig } = await import("../../servers/mcp-proxy.mjs");
  const { loadConfig } = await import("../../scripts/config.mjs");
  const env = {
    OPENVIKING_URL: "https://ov.example.com",
    OPENVIKING_API_KEY: "sk-parity",
    OPENVIKING_ACCOUNT: "acct",
    OPENVIKING_USER: "usr",
    OPENVIKING_AUTH_MODE: "trusted",
  };
  const hook = loadConfig(process.cwd(), { env });
  const proxy = readProxyConfig(env);
  assert.equal(proxy.mcpUrl, `${hook.baseUrl.replace(/\/+$/, "")}/mcp`);
  assert.equal(proxy.apiKey, hook.apiKey);
  assert.equal(proxy.account, hook.account);
  assert.equal(proxy.user, hook.user);
});
