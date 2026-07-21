import test from "node:test"
import assert from "node:assert/strict"
import { createServer } from "node:http"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { createMemorySessionManager } from "../lib/memory-session.mjs"

async function withTempDir(prefix, fn) {
  const dir = await mkdtemp(join(tmpdir(), prefix))
  try {
    return await fn(dir)
  } finally {
    await rm(dir, { recursive: true, force: true })
  }
}

async function withCaptureServer(fn) {
  const requests = []
  const server = createServer(async (req, res) => {
    let body = ""
    req.setEncoding("utf8")
    for await (const chunk of req) body += chunk
    requests.push({ method: req.method, url: req.url, body })

    res.setHeader("Content-Type", "application/json")
    if (req.url === "/health") {
      res.end(JSON.stringify({ status: "ok" }))
    } else if (req.url?.startsWith("/api/v1/sessions/") && req.url.endsWith("/messages/batch")) {
      res.end(JSON.stringify({ status: "ok", result: { accepted: true } }))
    } else if (req.url?.startsWith("/api/v1/sessions/")) {
      res.end(JSON.stringify({ status: "ok", result: { pending_tokens: 0 } }))
    } else {
      res.statusCode = 404
      res.end(JSON.stringify({ status: "error", error: { message: "not found" } }))
    }
  })

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve))
  try {
    const { port } = server.address()
    return await fn({ endpoint: `http://127.0.0.1:${port}`, requests })
  } finally {
    await new Promise((resolve) => server.close(resolve))
  }
}

function baseConfig(endpoint) {
  return {
    endpoint,
    apiKey: "",
    account: "",
    user: "",
    peerId: "",
    timeoutMs: 5000,
    autoCapture: true,
    captureAssistantTurns: true,
    captureToolMaxChars: 2000,
    captureMode: "semantic",
    captureMaxLength: 24000,
    commitTokenThreshold: 20000,
    commitKeepRecentCount: 10,
  }
}

test("autoCapture=false prevents OpenCode messages from being captured", async () => {
  await withCaptureServer(async ({ endpoint, requests }) => {
    await withTempDir("ov-oc-session-", async (dir) => {
      const manager = createMemorySessionManager({
        config: { ...baseConfig(endpoint), autoCapture: false },
        pluginRoot: dir,
      })

      await manager.init()
      await manager.handleEvent({ type: "session.created", properties: { info: { id: "oc-session-disabled" } } })
      await manager.handleEvent({
        type: "message.updated",
        properties: {
          info: {
            id: "msg-user-disabled",
            sessionID: "oc-session-disabled",
            role: "user",
          },
        },
      })
      await manager.handleEvent({
        type: "message.part.updated",
        properties: {
          part: {
            id: "part-user-disabled",
            messageID: "msg-user-disabled",
            sessionID: "oc-session-disabled",
            type: "text",
            text: "This message must not be captured.",
          },
        },
      })

      await manager.handleEvent({ type: "session.idle", sessionID: "oc-session-disabled" })
      await manager.flushAll({ commit: false })

      assert.equal(
        requests.some((request) => request.url?.endsWith("/messages/batch")),
        false,
        "disabled automatic capture must never POST session messages",
      )
    })
  })
})

test("autoCapture=false skips lifecycle commits but preserves explicit commits", async () => {
  await withCaptureServer(async ({ endpoint, requests }) => {
    await withTempDir("ov-oc-session-", async (dir) => {
      const manager = createMemorySessionManager({
        config: { ...baseConfig(endpoint), autoCapture: false },
        pluginRoot: dir,
      })

      await manager.init()
      await manager.handleEvent({ type: "session.created", properties: { info: { id: "oc-session-disabled" } } })
      await manager.handleEvent({ type: "session.compacted", sessionID: "oc-session-disabled" })
      await manager.flushAll({ commit: true })
      await manager.commitSession("oc-explicit-manual")

      const commitUrls = requests
        .filter((request) => request.method === "POST" && request.url?.endsWith("/commit"))
        .map((request) => request.url)
      assert.deepEqual(commitUrls, ["/api/v1/sessions/oc-explicit-manual/commit"])
    })
  })
})

test("session.idle event flushes pending OpenCode capture", async () => {
  await withCaptureServer(async ({ endpoint, requests }) => {
    await withTempDir("ov-oc-session-", async (dir) => {
      const manager = createMemorySessionManager({ config: baseConfig(endpoint), pluginRoot: dir })

      await manager.init()
      await manager.handleEvent({ type: "session.created", properties: { info: { id: "oc-session-1" } } })
      await manager.handleEvent({
        type: "message.updated",
        properties: {
          info: {
            id: "msg-user-1",
            sessionID: "oc-session-1",
            role: "user",
          },
        },
      })
      await manager.handleEvent({
        type: "message.part.updated",
        properties: {
          part: {
            id: "part-user-1",
            messageID: "msg-user-1",
            sessionID: "oc-session-1",
            type: "text",
            text: "Remember that OpenCode idle events must flush captures.",
          },
        },
      })

      await manager.handleEvent({ type: "session.idle", sessionID: "oc-session-1" })

      const addMessage = requests.find((request) => request.url === "/api/v1/sessions/oc-oc-session-1/messages/batch")
      assert.ok(addMessage, "session.idle should POST pending messages")
      const body = JSON.parse(addMessage.body)
      assert.equal(body.messages[0].role, "user")
      assert.match(body.messages[0].content, /idle events must flush captures/)
      await manager.flushAll({ commit: false })
    })
  })
})

test("assistant messages are captured even when finish is not stop", async () => {
  await withCaptureServer(async ({ endpoint, requests }) => {
    await withTempDir("ov-oc-session-", async (dir) => {
      const manager = createMemorySessionManager({ config: baseConfig(endpoint), pluginRoot: dir })

      await manager.init()
      await manager.handleEvent({
        type: "message.updated",
        properties: {
          info: {
            id: "msg-assistant-1",
            sessionID: "oc-session-2",
            role: "assistant",
            finish: "length",
          },
        },
      })
      await manager.handleEvent({
        type: "message.part.updated",
        properties: {
          part: {
            id: "part-assistant-1",
            messageID: "msg-assistant-1",
            sessionID: "oc-session-2",
            type: "text",
            text: "Partial assistant output still belongs in capture.",
          },
        },
      })

      await manager.handleEvent({ type: "session.idle", properties: { sessionID: "oc-session-2" } })

      const addMessage = requests.find((request) => request.url === "/api/v1/sessions/oc-oc-session-2/messages/batch")
      assert.ok(addMessage, "session.idle should capture non-stop assistant messages")
      const body = JSON.parse(addMessage.body)
      assert.equal(body.messages[0].role, "assistant")
      assert.match(body.messages[0].content, /Partial assistant output/)
      await manager.flushAll({ commit: false })
    })
  })
})
