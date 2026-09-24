import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { setTimeout as delay } from "node:timers/promises";
import { createMcpBridge, MCP_PROTOCOL_VERSION } from "../lib/mcp-bridge.mjs";
import { toPiResult, MAX_RESULT_BYTES, MAX_RESULT_LINES } from "../lib/mcp-result.mjs";

// Real SDK over HTTP: only the server response is controlled by each scenario.
async function fixture(t, handle = () => {}) {
  const requests = [];
  const server = createServer(async (req, res) => {
    if (req.method !== "POST") { res.writeHead(405).end(); return; }
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const message = JSON.parse(Buffer.concat(chunks));
    requests.push({ ...message, headers: req.headers });
    if (await handle(message, res)) return;
    if (message.id === undefined) { res.writeHead(202).end(); return; }
    const result = message.method === "initialize"
      ? { protocolVersion: MCP_PROTOCOL_VERSION, capabilities: { tools: {} }, serverInfo: { name: "fixture", version: "1" } }
      : message.method === "tools/list"
        ? { tools: [{ name: "echo", description: "Echo", inputSchema: { type: "object" } }] }
        : { content: [{ type: "text", text: JSON.stringify(message.params.arguments) }] };
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ jsonrpc: "2.0", id: message.id, result }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const cfg = { mcpUrl: "http://127.0.0.1:" + server.address().port + "/mcp", timeoutMs: 1000,
    apiKey: "key", sendIdentityHeaders: true, account: "acct", user: "user", peerId: "peer" };
  const bridge = createMcpBridge({ readConfig: () => cfg });
  t.after(async () => {
    await bridge.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  });
  return { bridge, cfg, requests, count: (method) => requests.filter((r) => r.method === method).length };
}

test("shares initialization, sends resolved headers, and pairs concurrent calls", async (t) => {
  const { bridge, requests, count } = await fixture(t);
  const states = await Promise.all(Array.from({ length: 10 }, () => bridge.connect()));
  assert.ok(states.every((s) => s.connected));
  assert.equal(count("initialize"), 1);
  assert.equal(count("tools/list"), 1);
  const results = await Promise.all(Array.from({ length: 20 }, (_, i) => bridge.callTool("echo", { i })));
  assert.deepEqual(results.map((r) => JSON.parse(r.content[0].text).i), Array.from({ length: 20 }, (_, i) => i));
  for (const req of requests) {
    assert.equal(req.headers.authorization, "Bearer key");
    assert.equal(req.headers["x-openviking-account"], "acct");
    assert.equal(req.headers["x-openviking-user"], "user");
    assert.equal(req.headers["x-openviking-actor-peer"], "peer");
  }
  assert.equal(requests[0].params.protocolVersion, MCP_PROTOCOL_VERSION);
});

for (const status of [401, 403, 404, 500]) {
  test("HTTP " + status + " fails once; the next call reconnects", async (t) => {
    let fail = true;
    const { bridge, count } = await fixture(t, (message, res) => {
      if (message.method === "tools/call" && fail) { fail = false; res.writeHead(status).end("rejected"); return true; }
    });
    await bridge.connect();
    await assert.rejects(bridge.callTool("echo", {}), /rejected/);
    assert.equal(count("tools/call"), 1);
    assert.equal(bridge.state.connected, false);
    await bridge.callTool("echo", {});
    assert.equal(count("initialize"), 2);
    assert.equal(count("tools/call"), 2);
  });
}

test("credential, identity and URL changes are picked up before the next call", async (t) => {
  const { bridge, cfg, requests, count } = await fixture(t);
  await bridge.connect();
  Object.assign(cfg, { apiKey: "rotated", sendIdentityHeaders: false, peerId: "next", mcpUrl: cfg.mcpUrl + "?updated=1" });
  await bridge.callTool("echo", {});
  assert.equal(count("initialize"), 2);
  const headers = requests.at(-1).headers;
  assert.equal(headers.authorization, "Bearer rotated");
  assert.equal(headers["x-openviking-account"], undefined);
  assert.equal(headers["x-openviking-user"], undefined);
  assert.equal(headers["x-openviking-actor-peer"], "next");
});

for (const stage of ["initialize", "notifications/initialized", "tools/list"]) {
  test("one handshake budget covers " + stage + " and a fresh instance can recover", async (t) => {
    let hang = true;
    const { bridge, count } = await fixture(t, (message) => hang && message.method === stage);
    const start = performance.now();
    assert.equal((await bridge.connect(70)).connected, false);
    assert.ok(performance.now() - start < 500);
    hang = false;
    assert.equal((await bridge.connect()).connected, true);
    assert.equal(count("initialize"), 2);
  });
}

test("tool budget includes connection time and never replays a timed out write", async (t) => {
  const { bridge, cfg, count } = await fixture(t, async (message) => {
    if (message.method === "initialize") await delay(80);
    if (message.method === "tools/call") await delay(300);
  });
  cfg.timeoutMs = 180;
  const start = performance.now();
  await assert.rejects(bridge.callTool("echo", { value: 1 }));
  assert.ok(performance.now() - start < 330);
  assert.equal(count("tools/call"), 1);
});

test("cancelling one tool leaves another concurrent call usable", async (t) => {
  const { bridge, count } = await fixture(t, (message) => message.method === "tools/call" && message.params.arguments.hang);
  await bridge.connect();
  const controller = new AbortController();
  const pending = bridge.callTool("echo", { hang: true }, { signal: controller.signal });
  const rejected = assert.rejects(pending);
  await delay(20);
  controller.abort();
  await rejected;
  assert.equal((await bridge.callTool("echo", { ok: true })).content[0].text, '{"ok":true}');
  assert.equal(count("initialize"), 1);
});

test("business errors do not reconnect", async (t) => {
  const { bridge, count } = await fixture(t, (message, res) => {
    if (message.method !== "tools/call") return;
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ jsonrpc: "2.0", id: message.id, result: { isError: true, content: [{ type: "text", text: "invalid URI" }] } }));
    return true;
  });
  await assert.rejects(bridge.callTool("echo", {}), /invalid URI/);
  await assert.rejects(bridge.callTool("echo", {}), /invalid URI/);
  assert.equal(count("initialize"), 1);
});

test("closing during startup never publishes a connection", async (t) => {
  const { bridge } = await fixture(t, async (message) => { if (message.method === "initialize") await delay(60); });
  const pending = bridge.connect();
  await delay(10);
  await bridge.close();
  await pending;
  assert.equal(bridge.state.connected, false);
  assert.equal(bridge.state.closed, true);
  await assert.rejects(bridge.callTool("echo", {}), /closed/);
});

test("result conversion preserves images, avoids structured echoes, and bounds all text together", () => {
  const image = { type: "image", data: "aGVsbG8=", mimeType: "image/png" };
  assert.deepEqual(toPiResult("read", { content: [{ type: "text", text: "hello" }, image], structuredContent: { result: "hello" } }).content,
    [{ type: "text", text: "hello" }, image]);
  for (const text of ["字".repeat(12000), "line\n".repeat(1100), " ".repeat(30000)]) {
    const result = toPiResult("read", { content: [{ type: "text", text }, image, { type: "text", text }] });
    const output = result.content.filter((b) => b.type === "text").map((b) => b.text).join("\n");
    assert.equal(result.details.truncated, true);
    assert.ok(Buffer.byteLength(output) <= MAX_RESULT_BYTES);
    assert.ok(output.split("\n").length <= MAX_RESULT_LINES);
    assert.ok(!output.includes("\ufffd"));
    assert.match(output, /Output truncated/);
    assert.ok(result.content.includes(image) || result.content.some((b) => b.type === "image"));
  }
});

test("large tool errors are bounded and non-text content remains readable", () => {
  assert.throws(() => toPiResult("read", { isError: true, content: [{ type: "text", text: "error".repeat(20000) }] }),
    (error) => Buffer.byteLength(error.message) <= MAX_RESULT_BYTES && error.message.includes("Output truncated"));
  const result = toPiResult("read", { content: [
    { type: "audio", data: "AAAA", mimeType: "audio/wav" },
    { type: "resource", resource: { uri: "resource-uri", text: "inline body" } },
    { type: "resource_link", uri: "linked-uri", name: "attachment" },
  ], structuredContent: { count: 3 } });
  const text = result.content.map((block) => block.text).join("\n");
  assert.match(text, /audio content omitted/);
  assert.match(text, /inline body/);
  assert.match(text, /linked-uri/);
  assert.match(text, /"count":3/);
});

test("an old in-flight call cannot invalidate the replacement connection", async (t) => {
  let received;
  const waiting = new Promise((resolve) => { received = resolve; });
  const { bridge, cfg, count } = await fixture(t, (message) => {
    if (message.method === "tools/call" && message.params.arguments.old) { received(); return true; }
  });
  await bridge.connect();
  const old = assert.rejects(bridge.callTool("echo", { old: true }));
  await waiting;
  cfg.apiKey = "replacement";
  await bridge.callTool("echo", { next: true });
  await old;
  await bridge.callTool("echo", { last: true });
  assert.equal(bridge.state.connected, true);
  assert.equal(count("initialize"), 2);
});

test("JSON-RPC argument errors fail the tool without reconnecting", async (t) => {
  const { bridge, count } = await fixture(t, (message, res) => {
    if (message.method !== "tools/call") return;
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ jsonrpc: "2.0", id: message.id, error: { code: -32602, message: "bad argument" } }));
    return true;
  });
  await assert.rejects(bridge.callTool("echo", {}), /bad argument/);
  await assert.rejects(bridge.callTool("echo", {}), /bad argument/);
  assert.equal(count("initialize"), 1);
});
