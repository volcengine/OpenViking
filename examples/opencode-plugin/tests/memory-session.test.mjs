import test from "node:test"
import assert from "node:assert/strict"
import { createServer } from "node:http"
import fs from "node:fs"
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

test("OpenCode tool parts preserve completed and error state fields", async () => {
  await withCaptureServer(async ({ endpoint, requests }) => {
    await withTempDir("ov-oc-session-", async (dir) => {
      const manager = createMemorySessionManager({ config: baseConfig(endpoint), pluginRoot: dir })
      const sessionID = "oc-tool-parts"
      const uri = "viking://user/test/memories/experiences/exchange.md"

      await manager.init()
      for (const [messageID, part] of [
        [
          "msg-tool-completed",
          {
            id: "part-tool-completed",
            sessionID,
            messageID: "msg-tool-completed",
            type: "tool",
            callID: "call-tool-completed",
            tool: "openviking_read",
            state: {
              status: "completed",
              input: { uris: uri },
              output: "experience contents",
            },
          },
        ],
        [
          "msg-tool-error",
          {
            id: "part-tool-error",
            sessionID,
            messageID: "msg-tool-error",
            type: "tool",
            callID: "call-tool-error",
            tool: "openviking_read",
            state: {
              status: "error",
              input: { uris: uri },
              error: "OpenViking request failed",
            },
          },
        ],
      ]) {
        await manager.handleEvent({
          type: "message.updated",
          properties: { info: { id: messageID, sessionID, role: "assistant" } },
        })
        await manager.handleEvent({
          type: "message.part.updated",
          properties: { part },
        })
      }

      await manager.handleEvent({ type: "session.idle", properties: { sessionID } })

      const addMessage = requests.find((request) => request.url === "/api/v1/sessions/oc-oc-tool-parts/messages/batch")
      assert.ok(addMessage, "session.idle should capture OpenCode tool parts")
      const body = JSON.parse(addMessage.body)
      assert.deepEqual(body.messages, [
        {
          role: "assistant",
          parts: [
            {
              type: "tool",
              tool_id: "call-tool-completed",
              tool_name: "openviking_read",
              tool_status: "completed",
              tool_input: { uris: uri },
              tool_output: "experience contents",
            },
          ],
        },
        {
          role: "assistant",
          parts: [
            {
              type: "tool",
              tool_id: "call-tool-error",
              tool_name: "openviking_read",
              tool_status: "error",
              tool_input: { uris: uri },
              tool_output: "OpenViking request failed",
            },
          ],
        },
      ])
      await manager.flushAll({ commit: false })
    })
  })
})

test("concurrent saves never race the shared state file (#3877)", async (t) => {
  // Widen the race window: concurrent saveState() calls share the same
  // `${statePath}.tmp` temp file. A slow writeFile keeps the shared .tmp
  // alive while a second writer arrives, and a slow rename lets a second
  // rename observe the file already moved — the exact ENOENT from #3877.
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
  const origWriteFile = fs.promises.writeFile
  const origRename = fs.promises.rename
  let raceDetected = false

  t.mock.method(fs.promises, "writeFile", async (...args) => {
    await delay(20)
    return origWriteFile.call(fs.promises, ...args)
  })
  t.mock.method(fs.promises, "rename", async (from, to) => {
    await delay(5)
    if (!fs.existsSync(from)) {
      // The shared .tmp was already moved by another concurrent save.
      raceDetected = true
      const err = new Error(
        `ENOENT: no such file or directory, rename '${from}' -> '${to}'`,
      )
      err.code = "ENOENT"
      throw err
    }
    return origRename.call(fs.promises, from, to)
  })

  await withTempDir("ov-oc-session-", async (dir) => {
    const manager = createMemorySessionManager({
      // autoCapture=false keeps the test fully offline (flushSession skips
      // the network); the endpoint is unreachable so health probes fail fast.
      config: { ...baseConfig("http://127.0.0.1:1"), autoCapture: false },
      pluginRoot: dir,
    })

    await manager.handleEvent({ type: "session.created", properties: { info: { id: "sess-a" } } })
    await manager.handleEvent({ type: "session.created", properties: { info: { id: "sess-b" } } })
    await manager.handleEvent({ type: "session.created", properties: { info: { id: "sess-c" } } })

    // Fire several flushAll() calls concurrently — each one calls saveState()
    // against the same shared `.tmp` file, racing the debounced saves above.
    const racing = Promise.all([
      manager.flushAll(),
      manager.flushAll(),
      manager.flushAll(),
    ])

    // Mutate in-memory state while the racing saves are in flight: with the
    // race, whichever rename runs first moves the shared .tmp away and the
    // others fail with ENOENT, leaving a stale snapshot on disk. With
    // serialized saves the final save is the newest snapshot.
    await delay(30)
    await manager.handleEvent({ type: "session.created", properties: { info: { id: "sess-d" } } })
    await racing
    await manager.flushAll() // final serialized save, includes sess-d

    const statePath = join(dir, "openviking-session-state.json")
    const data = JSON.parse(await fs.promises.readFile(statePath, "utf8"))
    assert.equal(data.version, 2)
    assert.deepEqual(
      Object.keys(data.sessions).sort(),
      ["sess-a", "sess-b", "sess-c", "sess-d"],
    )
    // Core assertion: concurrent saves must never race each other off the
    // shared `.tmp` file. With serialized saves no ENOENT can occur.
    assert.equal(raceDetected, false)
  })
})
