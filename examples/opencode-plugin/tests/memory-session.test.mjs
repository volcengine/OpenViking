import test from "node:test"
import assert from "node:assert/strict"
import { createServer } from "node:http"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { createMemorySessionManager } from "../lib/memory-session.mjs"
import { createQueueScope, enqueue, listPending } from "../lib/shared/pending-queue.mjs"

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
    } else if (req.url?.startsWith("/api/v1/sessions/") && req.url.endsWith("/messages")) {
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

test("autoCapture=false defers queued automatic writes but replays manual commits", async () => {
  await withCaptureServer(async ({ endpoint, requests }) => {
    await withTempDir("ov-oc-session-", async (dir) => {
      const previousPendingDir = process.env.OPENVIKING_PENDING_DIR
      const previousKeyFile = process.env.OPENVIKING_QUEUE_SCOPE_KEY_FILE
      process.env.OPENVIKING_PENDING_DIR = join(dir, "pending")
      process.env.OPENVIKING_QUEUE_SCOPE_KEY_FILE = join(dir, "queue-scope.key")
      try {
        const config = { ...baseConfig(endpoint), autoCapture: false }
        const scope = await createQueueScope({
          producer: "opencode", baseUrl: config.endpoint, account: config.account,
          user: config.user, apiKey: config.apiKey,
        })
        await enqueue(scope,
          "addMessage",
          "oc-queued-auto",
          { role: "user", content: "Queued automatic capture must remain deferred." },
          { provenance: "autoCapture" },
        )
        await enqueue(scope,
          "commitSession",
          "oc-queued-manual",
          { keep_recent_count: 0 },
          { provenance: "manual" },
        )

        const manager = createMemorySessionManager({
          config,
          pluginRoot: dir,
        })
        await manager.init()
        await manager.handleEvent({
          type: "session.created",
          properties: { info: { id: "oc-session-after-disabled-init" } },
        })

        assert.equal(
          requests.some((request) => request.url === "/api/v1/sessions/oc-queued-auto/messages"),
          false,
          "disabled automatic capture must not replay queued conversation writes",
        )
        assert.equal(
          requests.some((request) => request.url === "/api/v1/sessions/oc-queued-manual/commit"),
          true,
          "manual queued commits must remain replayable",
        )
        const pending = await listPending(scope)
        assert.equal(pending.length, 1)
        assert.equal(pending[0].entry.provenance, "autoCapture")
        await manager.flushAll({ commit: false })
      } finally {
        if (previousPendingDir === undefined) delete process.env.OPENVIKING_PENDING_DIR
        else process.env.OPENVIKING_PENDING_DIR = previousPendingDir
        if (previousKeyFile === undefined) delete process.env.OPENVIKING_QUEUE_SCOPE_KEY_FILE
        else process.env.OPENVIKING_QUEUE_SCOPE_KEY_FILE = previousKeyFile
      }
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

      const addMessage = requests.find((request) => request.url === "/api/v1/sessions/oc-oc-session-1/messages")
      assert.ok(addMessage, "session.idle should POST pending messages")
      const body = JSON.parse(addMessage.body)
      assert.equal(body.role, "user")
      assert.match(body.content, /idle events must flush captures/)
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

      const addMessage = requests.find((request) => request.url === "/api/v1/sessions/oc-oc-session-2/messages")
      assert.ok(addMessage, "session.idle should capture non-stop assistant messages")
      const body = JSON.parse(addMessage.body)
      assert.equal(body.role, "assistant")
      assert.match(body.content, /Partial assistant output/)
      await manager.flushAll({ commit: false })
    })
  })
})
