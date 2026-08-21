import test from "node:test"
import assert from "node:assert/strict"
import { createServer } from "node:http"
import fs from "node:fs"
import { mkdtemp, rm, readdir, writeFile, utimes } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { createMemorySessionManager } from "../lib/memory-session.mjs"
import { initLogger } from "../lib/utils.mjs"

async function withTempDir(prefix, fn) {
  const dir = await mkdtemp(join(tmpdir(), prefix))
  try {
    return await fn(dir)
  } finally {
    await rm(dir, { recursive: true, force: true })
  }
}

// Find a PID that is definitely not running. process.kill(pid, 0) throws
// ESRCH for a non-existent PID; probe upward from a large base so the test
// does not rely on a hard-coded value that could be alive on high-pid_max
// systems.
function findDeadPid() {
  for (let pid = 999983; pid < 4194304; pid += 104729) {
    try {
      process.kill(pid, 0)
    } catch (err) {
      if (err?.code === "ESRCH") return pid
    }
  }
  throw new Error("could not find a dead PID")
}

async function withCaptureServer(fn, options = {}) {
  const requests = []
  const { onBatch } = options
  const server = createServer(async (req, res) => {
    let body = ""
    req.setEncoding("utf8")
    for await (const chunk of req) body += chunk
    requests.push({ method: req.method, url: req.url, body })

    res.setHeader("Content-Type", "application/json")
    if (req.url === "/health") {
      res.end(JSON.stringify({ status: "ok" }))
    } else if (req.url?.startsWith("/api/v1/sessions/") && req.url.endsWith("/messages/batch")) {
      // Optional hook lets a test hold a batch open so it can observe an
      // in-flight send (e.g. a periodic flush) racing another operation.
      if (typeof onBatch === "function") await onBatch(req.url)
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

async function seedUserMessage(manager, sessionId, messageId, text) {
  await manager.handleEvent({
    type: "message.updated",
    properties: { info: { id: messageId, sessionID: sessionId, role: "user" } },
  })
  await manager.handleEvent({
    type: "message.part.updated",
    properties: {
      part: { id: `${messageId}-part`, messageID: messageId, sessionID: sessionId, type: "text", text },
    },
  })
}

test("concurrent flushSession on the same session sends each pending batch once", async () => {
  await withCaptureServer(async ({ endpoint, requests }) => {
    await withTempDir("ov-oc-session-", async (dir) => {
      const manager = createMemorySessionManager({ config: baseConfig(endpoint), pluginRoot: dir })
      await manager.init()
      await manager.handleEvent({ type: "session.created", properties: { info: { id: "oc-gate" } } })
      await seedUserMessage(manager, "oc-gate", "msg-gate-1", "Gate serialization must prevent double sends.")

      // Fire two overlapping flushes without awaiting the first.
      await Promise.all([
        manager.flushSession("oc-gate", { commit: false }),
        manager.flushSession("oc-gate", { commit: false }),
      ])

      const batches = requests.filter((r) => r.url === "/api/v1/sessions/oc-oc-gate/messages/batch")
      assert.equal(batches.length, 1, "overlapping flushes must not double-send the same message")
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

test("flushAll shutdown drains an in-flight periodic flush before its own loop", async () => {
  // Gate the first batch send so a periodic flush is genuinely mid-flight when
  // flushAll({shutdown:true}) runs. This exercises the drain branch
  // (`if (periodicFlushPromise) await periodicFlushPromise`) in flushAll and
  // proves the shutdown waits for in-flight periodic work instead of racing it.
  let releaseBatch
  const batchGate = new Promise((resolve) => {
    releaseBatch = resolve
  })
  let firstBatchSeen
  const firstBatch = new Promise((resolve) => {
    firstBatchSeen = resolve
  })
  let batchCount = 0

  await withCaptureServer(
    async ({ endpoint, requests }) => {
      await withTempDir("ov-oc-session-", async (dir) => {
        const manager = createMemorySessionManager({
          config: { ...baseConfig(endpoint), periodicFlushIntervalMs: 10 },
          pluginRoot: dir,
        })
        await manager.init()
        await manager.handleEvent({ type: "session.created", properties: { info: { id: "oc-drain" } } })
        await seedUserMessage(manager, "oc-drain", "msg-drain-1", "Teardown must drain periodic work.")

        // Wait until the periodic timer has actually started a batch send that
        // is now blocked in the gate, then tear down while it is in-flight.
        await firstBatch
        const shutdown = manager.flushAll({ commit: true, shutdown: true })
        // Let the in-flight periodic batch complete; flushAll must have awaited it.
        releaseBatch()
        await shutdown

        const batches = requests.filter((r) => r.url === "/api/v1/sessions/oc-oc-drain/messages/batch")
        assert.equal(batches.length, 1, "shutdown must not double-send the in-flight periodic batch")
        const commits = requests.filter((r) => r.method === "POST" && r.url === "/api/v1/sessions/oc-oc-drain/commit")
        assert.equal(commits.length, 1, "shutdown with commit:true must commit exactly once")
        // The gated batch must have been fully sent before the commit fired.
        const order = requests
          .filter((r) => r.method === "POST" && r.url?.startsWith("/api/v1/sessions/oc-oc-drain/"))
          .map((r) => r.url)
        assert.deepEqual(
          order,
          ["/api/v1/sessions/oc-oc-drain/messages/batch", "/api/v1/sessions/oc-oc-drain/commit"],
          "the drained periodic batch must complete before the shutdown commit",
        )
      })
    },
    {
      onBatch: async (url) => {
        if (url === "/api/v1/sessions/oc-oc-drain/messages/batch") {
          batchCount += 1
          if (batchCount === 1) {
            firstBatchSeen()
            await batchGate
          }
        }
      },
    },
  )
})

test("commitSession routes the send through the per-session gate", async () => {
  await withCaptureServer(async ({ endpoint, requests }) => {
    await withTempDir("ov-oc-session-", async (dir) => {
      const manager = createMemorySessionManager({ config: baseConfig(endpoint), pluginRoot: dir })
      await manager.init()
      await manager.handleEvent({ type: "session.created", properties: { info: { id: "oc-tool" } } })
      await seedUserMessage(manager, "oc-tool", "msg-tool-1", "Tool commit must flush pending first.")

      await manager.commitSession("oc-oc-tool", "oc-tool")

      const batches = requests.filter((r) => r.url === "/api/v1/sessions/oc-oc-tool/messages/batch")
      assert.equal(batches.length, 1, "commitSession must flush pending messages before committing")
      const order = requests
        .filter((r) => r.method === "POST" && r.url?.startsWith("/api/v1/sessions/oc-oc-tool/"))
        .map((r) => r.url)
      assert.deepEqual(
        order,
        ["/api/v1/sessions/oc-oc-tool/messages/batch", "/api/v1/sessions/oc-oc-tool/commit"],
        "the batch send must happen before the commit",
      )
    })
  })
})

test("startup sweep reclaims orphan temp files from a dead PID but keeps live/recent ones", async () => {
  await withCaptureServer(async ({ endpoint }) => {
    await withTempDir("ov-oc-session-", async (dir) => {
      const statePath = join(dir, "openviking-session-state.json")
      // Dead PID: derived at runtime so the test is not flaky on systems with
      // a high pid_max where a hard-coded large PID could be alive.
      const deadPid = findDeadPid()
      const deadTmp = `${statePath}.${deadPid}.0.tmp`
      // Live PID (this test process), recent mtime -> must be kept.
      const liveRecentTmp = `${statePath}.${process.pid}.0.tmp`
      // Live PID but stale mtime (simulated recycled PID) -> must be reclaimed.
      const liveStaleTmp = `${statePath}.${process.pid}.1.tmp`
      await writeFile(deadTmp, "orphan")
      await writeFile(liveRecentTmp, "in-flight")
      await writeFile(liveStaleTmp, "recycled-pid-orphan")
      const stale = new Date(Date.now() - 30 * 60 * 1000)
      await utimes(liveStaleTmp, stale, stale)

      const manager = createMemorySessionManager({ config: baseConfig(endpoint), pluginRoot: dir })
      await manager.init()

      const remaining = (await readdir(dir)).filter((n) => n.endsWith(".tmp"))
      assert.ok(
        !remaining.includes(`openviking-session-state.json.${deadPid}.0.tmp`),
        "dead-PID orphan must be removed",
      )
      assert.ok(
        remaining.includes(`openviking-session-state.json.${process.pid}.0.tmp`),
        "recent live-PID temp must be kept",
      )
      assert.ok(
        !remaining.includes(`openviking-session-state.json.${process.pid}.1.tmp`),
        "stale live-PID temp (recycled PID) must be reclaimed",
      )
      await manager.flushAll({ commit: false, shutdown: true })
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

test("explicit commit writes the response trace_id to the plugin log", async () => {
  const requests = []
  const server = createServer(async (req, res) => {
    let body = ""
    req.setEncoding("utf8")
    for await (const chunk of req) body += chunk
    requests.push({ method: req.method, url: req.url, body })
    res.setHeader("Content-Type", "application/json")
    res.end(JSON.stringify({
      status: "ok",
      result: {
        status: "accepted",
        task_id: "task-opencode-trace",
        trace_id: "trace-opencode-commit",
      },
    }))
  })
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve))
  try {
    await withTempDir("ov-oc-session-trace-", async (dir) => {
      const { port } = server.address()
      initLogger(dir)
      const manager = createMemorySessionManager({
        config: { ...baseConfig(`http://127.0.0.1:${port}`), autoCapture: false },
        pluginRoot: dir,
      })

      const result = await manager.commitSession("oc-explicit-trace")
      assert.equal(result.traceId, "trace-opencode-commit")
      assert.equal(requests[0].url, "/api/v1/sessions/oc-explicit-trace/commit")
      const raw = await fs.promises.readFile(join(dir, "openviking-memory.log"), "utf8")
      assert.match(raw, /"trace_id":"trace-opencode-commit"/)
    })
  } finally {
    await new Promise((resolve) => server.close(resolve))
  }
})
