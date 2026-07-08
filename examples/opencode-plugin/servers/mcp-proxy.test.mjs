import test from "node:test"
import assert from "node:assert/strict"
import { createServer } from "node:http"
import { Writable } from "node:stream"
import { createOpenVikingMcpProxy } from "./mcp-proxy.mjs"

function jsonRpc(id, result = {}) {
  return { jsonrpc: "2.0", id, result }
}

function sseMessage(obj) {
  return `event: message\r\ndata: ${JSON.stringify(obj)}\r\n\r\n`
}

async function readBody(req) {
  const chunks = []
  for await (const chunk of req) chunks.push(chunk)
  return Buffer.concat(chunks).toString("utf-8")
}

async function withServer(handler, fn) {
  const requests = []
  const server = createServer(async (req, res) => {
    const body = await readBody(req)
    const entry = {
      method: req.method,
      url: req.url,
      headers: req.headers,
      body: body ? JSON.parse(body) : null,
    }
    requests.push(entry)
    await handler(req, res, entry)
  })
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve))
  try {
    const { port } = server.address()
    return await fn({ url: `http://127.0.0.1:${port}/mcp`, requests })
  } finally {
    await new Promise((resolve) => server.close(resolve))
  }
}

function makeProxy(url) {
  const out = []
  const stdout = new Writable({
    write(chunk, _encoding, callback) {
      out.push(chunk.toString("utf-8"))
      callback()
    },
  })
  const proxy = createOpenVikingMcpProxy({
    stdout,
    readConfig: () => ({
      mcpUrl: url,
      apiKey: "test-key",
      account: "acct",
      user: "user",
      peerId: "peer",
      timeoutMs: 5000,
      debug: false,
      debugLogPath: "",
      credentialSource: "test",
      credentialPath: "",
      watchedPaths: [],
    }),
    loggerFactory: () => ({ log() {}, logError() {} }),
  })
  return {
    proxy,
    async messages() {
      await new Promise((resolve) => setImmediate(resolve))
      return out.join("").trim().split("\n").filter(Boolean).map((line) => JSON.parse(line))
    },
  }
}

test("OpenCode MCP proxy forwards initialize with auth and identity headers", async () => {
  await withServer((_req, res, entry) => {
    assert.equal(entry.method, "POST")
    assert.equal(entry.headers.authorization, "Bearer test-key")
    assert.equal(entry.headers["x-openviking-account"], "acct")
    assert.equal(entry.headers["x-openviking-user"], "user")
    assert.equal(entry.headers["x-openviking-actor-peer"], "peer")
    res.writeHead(200, {
      "content-type": "text/event-stream",
      "mcp-session-id": "sid-oc",
    })
    res.end(sseMessage(jsonRpc(1, { protocolVersion: "2025-06-18" })))
  }, async ({ url, requests }) => {
    const { proxy, messages } = makeProxy(url)
    await proxy.handleMessage({
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: { protocolVersion: "2025-06-18" },
    })
    assert.deepEqual(await messages(), [jsonRpc(1, { protocolVersion: "2025-06-18" })])
    assert.equal(requests.length, 1)
  })
})
