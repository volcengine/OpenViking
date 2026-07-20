import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Writable } from "node:stream";
import test from "node:test";
import { createOpenVikingMcpProxy } from "./mcp-proxy.mjs";

function jsonRpc(id, result = {}) {
  return { jsonrpc: "2.0", id, result };
}

function sseMessage(obj) {
  return `event: message\r\ndata: ${JSON.stringify(obj)}\r\n\r\n`;
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf-8");
}

async function withServer(handler, fn) {
  const requests = [];
  const server = createServer(async (req, res) => {
    const body = await readBody(req);
    let parsed = null;
    try {
      parsed = body ? JSON.parse(body) : null;
    } catch { /* test handler may inspect raw body */ }
    const entry = { method: req.method, url: req.url, headers: req.headers, body: parsed, rawBody: body };
    requests.push(entry);
    await handler(req, res, entry, requests);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const { port } = server.address();
    return await fn({ url: `http://127.0.0.1:${port}/mcp`, requests });
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

function makeProxy({ url, configOverrides = {}, stdout, localToolProvider, readConfig } = {}) {
  const out = [];
  const writable = stdout || new Writable({
    write(chunk, _encoding, callback) {
      out.push(chunk.toString("utf-8"));
      callback();
    },
  });
  const proxy = createOpenVikingMcpProxy({
    stdout: writable,
    readConfig: readConfig || (() => ({
      mcpUrl: url,
      apiKey: "test-key",
      account: "default",
      user: "zeus",
      peerId: "peer-a",
      timeoutMs: 5000,
      debug: false,
      debugLogPath: "",
      credentialSource: "test",
      credentialPath: "",
      watchedPaths: [],
      ...configOverrides,
    })),
    loggerFactory: () => ({ log() {}, logError() {} }),
    localToolProvider,
  });
  return {
    proxy,
    out,
    async messages() {
      await new Promise((resolve) => setImmediate(resolve));
      return out.join("").trim().split("\n").filter(Boolean).map((line) => JSON.parse(line));
    },
  };
}

test("captures initialize session id and forwards SSE JSON-RPC response", async () => {
  await withServer((_req, res, entry) => {
    assert.equal(entry.method, "POST");
    assert.equal(entry.headers.authorization, "Bearer test-key");
    assert.equal(entry.headers["x-openviking-account"], "default");
    assert.equal(entry.headers["x-openviking-user"], "zeus");
    assert.equal(entry.headers["x-openviking-actor-peer"], "peer-a");
    assert.equal(entry.headers["mcp-protocol-version"], "2025-06-18");
    assert.equal(entry.headers["mcp-session-id"], undefined);
    res.writeHead(200, {
      "content-type": "text/event-stream",
      "mcp-session-id": "sid-1",
    });
    res.end(`: keepalive\r\n${sseMessage(jsonRpc(1, { protocolVersion: "2025-06-18" }))}`);
  }, async ({ url, requests }) => {
    const { proxy, messages } = makeProxy({ url });
    await proxy.handleMessage({
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: { protocolVersion: "2025-06-18" },
    });
    assert.deepEqual(await messages(), [jsonRpc(1, { protocolVersion: "2025-06-18" })]);
    assert.equal(requests.length, 1);
  });
});

test("never forwards the client's un-negotiated protocol version header", async () => {
  await withServer((_req, res, entry) => {
    // Strict upstreams 400 on unsupported MCP-Protocol-Version before
    // negotiation runs, so the header must stay on a proxy-known version
    // even when the client asks for a newer spec (Trae sends 2025-11-25).
    assert.equal(entry.headers["mcp-protocol-version"], "2025-06-18");
    res.writeHead(200, {
      "content-type": "application/json",
      "mcp-session-id": "sid-np",
    });
    if (entry.body.method === "initialize") {
      res.end(JSON.stringify(jsonRpc(entry.body.id, { protocolVersion: "2025-06-18" })));
      return;
    }
    res.end(JSON.stringify(jsonRpc(entry.body.id, { tools: [] })));
  }, async ({ url, requests }) => {
    const { proxy } = makeProxy({ url });
    await proxy.handleMessage({
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: { protocolVersion: "2025-11-25" },
    });
    await proxy.handleMessage({ jsonrpc: "2.0", id: 2, method: "tools/list" });
    assert.equal(requests.length, 2);
    // The initialize body still carries the client's ask end-to-end; only the
    // transport header is pinned until the server negotiates.
    assert.equal(requests[0].body.params.protocolVersion, "2025-11-25");
  });
});

test("adopts the server-negotiated protocol version for subsequent requests", async () => {
  await withServer((_req, res, entry) => {
    if (entry.body.method === "initialize") {
      res.writeHead(200, {
        "content-type": "application/json",
        "mcp-session-id": "sid-nv",
      });
      res.end(JSON.stringify(jsonRpc(entry.body.id, { protocolVersion: "2025-03-26" })));
      return;
    }
    assert.equal(entry.headers["mcp-protocol-version"], "2025-03-26");
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(jsonRpc(entry.body.id, { tools: [] })));
  }, async ({ url, requests }) => {
    const { proxy } = makeProxy({ url });
    // Client asks 2025-06-18 but the server negotiates down: follow-up
    // requests must carry the response's version, not the request's.
    await proxy.handleMessage({
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: { protocolVersion: "2025-06-18" },
    });
    await proxy.handleMessage({ jsonrpc: "2.0", id: 2, method: "tools/list" });
    assert.equal(requests.length, 2);
  });
});

test("forwards notifications and writes no stdout for HTTP 202", async () => {
  await withServer((_req, res, entry) => {
    assert.equal(entry.body.method, "notifications/initialized");
    res.writeHead(202, { "content-type": "application/json" });
    res.end("");
  }, async ({ url }) => {
    const { proxy, messages } = makeProxy({ url });
    await proxy.handleMessage({ jsonrpc: "2.0", method: "notifications/initialized" });
    assert.deepEqual(await messages(), []);
  });
});

test("uses independent POST requests for concurrent calls", async () => {
  await withServer(async (_req, res, entry) => {
    const delay = entry.body.id === 1 ? 50 : 5;
    await new Promise((resolve) => setTimeout(resolve, delay));
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(jsonRpc(entry.body.id, { name: entry.body.method })));
  }, async ({ url, requests }) => {
    const { proxy, messages } = makeProxy({ url });
    await Promise.all([
      proxy.handleMessage({ jsonrpc: "2.0", id: 1, method: "tools/list" }),
      proxy.handleMessage({ jsonrpc: "2.0", id: 2, method: "tools/call" }),
    ]);
    const ids = (await messages()).map((m) => m.id).sort();
    assert.deepEqual(ids, [1, 2]);
    assert.equal(requests.length, 2);
  });
});

test("appends local tools to the upstream tools/list result", async () => {
  const localTools = [
    { name: "search_experience", description: "Search experiences", inputSchema: { type: "object" } },
    { name: "read_experience", description: "Read an experience", inputSchema: { type: "object" } },
  ];
  await withServer((_req, res, entry) => {
    assert.equal(entry.body.method, "tools/list");
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(jsonRpc(entry.body.id, {
      tools: [{ name: "find", description: "Find context", inputSchema: { type: "object" } }],
    })));
  }, async ({ url, requests }) => {
    const { proxy, messages } = makeProxy({
      url,
      localToolProvider: {
        listTools: () => localTools,
        async callTool() { return null; },
      },
    });

    await proxy.handleMessage({ jsonrpc: "2.0", id: 20, method: "tools/list" });

    const [response] = await messages();
    assert.deepEqual(response.result.tools.map((tool) => tool.name), [
      "find",
      "search_experience",
      "read_experience",
    ]);
    assert.equal(requests.length, 1);
  });
});

test("replaces an upstream tool definition when the local tool has the same name", async () => {
  const localTool = {
    name: "search_experience",
    description: "Local experience search",
    inputSchema: { type: "object", required: ["query"] },
  };
  await withServer((_req, res, entry) => {
    assert.equal(entry.body.method, "tools/list");
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(jsonRpc(entry.body.id, {
      tools: [
        { name: "find", description: "Find context", inputSchema: { type: "object" } },
        {
          name: "search_experience",
          description: "Upstream experience search",
          inputSchema: { type: "object", required: ["text"] },
        },
      ],
    })));
  }, async ({ url }) => {
    const { proxy, messages } = makeProxy({
      url,
      localToolProvider: {
        listTools: () => [localTool],
        async callTool() { return null; },
      },
    });

    await proxy.handleMessage({ jsonrpc: "2.0", id: 22, method: "tools/list" });

    const [response] = await messages();
    assert.deepEqual(response.result.tools, [
      { name: "find", description: "Find context", inputSchema: { type: "object" } },
      localTool,
    ]);
  });
});

test("handles local tools without forwarding tools/call upstream", async () => {
  const calls = [];
  const { proxy, messages } = makeProxy({
    url: "http://127.0.0.1:1/mcp",
    localToolProvider: {
      listTools: () => [{ name: "search_experience" }],
      async callTool(params, context) {
        calls.push({ params, context });
        return { content: [{ type: "text", text: '{"results":[]}' }] };
      },
    },
  });

  await proxy.handleMessage({
    jsonrpc: "2.0",
    id: 21,
    method: "tools/call",
    params: { name: "search_experience", arguments: { query: "换货" } },
  });

  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].params, {
    name: "search_experience",
    arguments: { query: "换货" },
  });
  assert.equal(calls[0].context.config.user, "zeus");
  assert.deepEqual(await messages(), [
    jsonRpc(21, { content: [{ type: "text", text: '{"results":[]}' }] }),
  ]);
});

test("reloads changed credential files before calling a local tool", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "openviking-mcp-proxy-"));
  const credentialPath = join(dir, "ovcli.conf");
  writeFileSync(credentialPath, "old-key", "utf-8");
  t.after(() => rmSync(dir, { recursive: true, force: true }));

  let apiKey = "old-key";
  const receivedKeys = [];
  const readConfig = () => ({
    mcpUrl: "http://127.0.0.1:1/mcp",
    apiKey,
    account: "default",
    user: "zeus",
    peerId: "peer-a",
    timeoutMs: 5000,
    debug: false,
    debugLogPath: "",
    credentialSource: "ovcli",
    credentialPath,
    watchedPaths: [credentialPath],
  });
  const { proxy, messages } = makeProxy({
    readConfig,
    localToolProvider: {
      listTools: () => [{ name: "search_experience" }],
      async callTool(_params, context) {
        receivedKeys.push(context.config.apiKey);
        return { content: [{ type: "text", text: '{"results":[]}' }] };
      },
    },
  });

  apiKey = "new-key";
  writeFileSync(credentialPath, "new-key-with-different-size", "utf-8");
  await proxy.handleMessage({
    jsonrpc: "2.0",
    id: 22,
    method: "tools/call",
    params: { name: "search_experience", arguments: { query: "换货" } },
  });

  assert.deepEqual(receivedKeys, ["new-key"]);
  assert.equal((await messages())[0].id, 22);
});

test("reinitializes after 404 and retries the original request once", async () => {
  let call = 0;
  await withServer((_req, res, entry) => {
    call += 1;
    if (call === 1) {
      res.writeHead(200, { "content-type": "application/json", "mcp-session-id": "sid-old" });
      res.end(JSON.stringify(jsonRpc(1, {})));
      return;
    }
    if (call === 2) {
      assert.equal(entry.headers["mcp-session-id"], "sid-old");
      res.writeHead(404, { "content-type": "application/json" });
      res.end(JSON.stringify({ jsonrpc: "2.0", id: entry.body.id, error: { code: -32000, message: "session missing" } }));
      return;
    }
    if (call === 3) {
      assert.equal(entry.body.method, "initialize");
      assert.match(String(entry.body.id), /^openviking-proxy-reinit-/);
      assert.equal(entry.headers["mcp-session-id"], undefined);
      res.writeHead(200, { "content-type": "application/json", "mcp-session-id": "sid-new" });
      res.end(JSON.stringify(jsonRpc(entry.body.id, {})));
      return;
    }
    assert.equal(entry.headers["mcp-session-id"], "sid-new");
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(jsonRpc(2, { ok: true })));
  }, async ({ url, requests }) => {
    const { proxy, messages } = makeProxy({ url });
    await proxy.handleMessage({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18" } });
    await proxy.handleMessage({ jsonrpc: "2.0", id: 2, method: "tools/list" });
    const responses = await messages();
    assert.equal(responses.at(-1).id, 2);
    assert.deepEqual(responses.at(-1).result, { ok: true });
    assert.equal(requests.length, 4);
  });
});

test("maps 401/403 to actionable JSON-RPC auth errors", async () => {
  await withServer((_req, res) => {
    res.writeHead(401, { "content-type": "application/json" });
    res.end(JSON.stringify({ jsonrpc: "2.0", id: null, error: { code: -32001, message: "bad token" } }));
  }, async ({ url }) => {
    const { proxy, messages } = makeProxy({ url });
    await proxy.handleMessage({ jsonrpc: "2.0", id: 7, method: "tools/list" });
    const [msg] = await messages();
    assert.equal(msg.id, 7);
    assert.equal(msg.error.code, -32001);
    assert.match(msg.error.message, /authentication failed/i);
    assert.equal(msg.error.data.status, 401);
    assert.equal(msg.error.data.serverMessage, "bad token");
  });
});

test("DELETEs the upstream MCP session on close", async () => {
  await withServer((_req, res, entry) => {
    if (entry.method === "POST") {
      res.writeHead(200, { "content-type": "application/json", "mcp-session-id": "sid-close" });
      res.end(JSON.stringify(jsonRpc(1, {})));
      return;
    }
    assert.equal(entry.method, "DELETE");
    assert.equal(entry.headers["mcp-session-id"], "sid-close");
    res.writeHead(204);
    res.end("");
  }, async ({ url, requests }) => {
    const { proxy } = makeProxy({ url });
    await proxy.handleMessage({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18" } });
    await proxy.closeSession();
    assert.equal(requests.at(-1).method, "DELETE");
  });
});
