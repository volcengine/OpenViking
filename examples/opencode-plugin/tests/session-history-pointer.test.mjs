import test from "node:test"
import assert from "node:assert/strict"
import { createServer } from "node:http"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { createMemorySessionManager } from "../lib/memory-session.mjs"
import { createSessionInject } from "../lib/session-inject.mjs"

async function withOpenViking(fn) {
  const server = createServer(async (req, res) => {
    for await (const _chunk of req) { /* drain */ }
    res.setHeader("Content-Type", "application/json")
    res.end(JSON.stringify({ status: "ok", result: { pending_tokens: 0 } }))
  })
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve))
  const dir = await mkdtemp(join(tmpdir(), "ov-oc-pointer-"))
  try {
    return await fn({ endpoint: `http://127.0.0.1:${server.address().port}`, dir })
  } finally {
    await new Promise((resolve) => server.close(resolve))
    await rm(dir, { recursive: true, force: true })
  }
}

function setup({ endpoint, dir }, overrides = {}) {
  // Profile injection off: the pointer is independent of it and needs no server.
  const config = {
    endpoint,
    timeoutMs: 5000,
    autoCapture: true,
    noAutoInject: true,
    sessionHistoryPointer: true,
    commitTokenThreshold: 20000,
    ...overrides,
  }
  const sessionManager = createMemorySessionManager({ config, pluginRoot: dir })
  const inject = createSessionInject({ config, sessionManager })
  let n = 0
  const nextMessage = (sessionID) => inject.buildSessionContext({ sessionID, messageID: `msg-${++n}` })
  return { sessionManager, nextMessage }
}

const compacted = (sessionID) => ({ type: "session.compacted", sessionID })

test("the first message after each compaction carries the pointer, once", async () => {
  await withOpenViking(async (server) => {
    const { sessionManager, nextMessage } = setup(server)
    await sessionManager.init()
    await sessionManager.handleEvent({ type: "session.created", properties: { info: { id: "ses_main" } } })
    assert.ok(!(await nextMessage("ses_main")))

    await sessionManager.handleEvent(compacted("ses_main"))
    const block = await nextMessage("ses_main")
    assert.match(block, /^<openviking-context source="session-history">/)
    assert.match(block, /full record of this session as oc-ses_main/)
    // OpenCode's MCP server cannot tell which session calls it.
    assert.match(block, /Pass session="oc-ses_main" with each call\./)
    assert.ok(!(await nextMessage("ses_main")))

    await new Promise((resolve) => setTimeout(resolve, 5))
    await sessionManager.handleEvent(compacted("ses_main"))
    assert.match(await nextMessage("ses_main"), /oc-ses_main/)
  })
})

test("a delivered pointer stays delivered across a restart, a pending one survives it", async () => {
  await withOpenViking(async (server) => {
    const first = setup(server)
    await first.sessionManager.init()
    await first.sessionManager.handleEvent(compacted("ses_a"))
    await first.sessionManager.handleEvent(compacted("ses_b"))
    assert.match(await first.nextMessage("ses_a"), /oc-ses_a/)
    await first.sessionManager.flushAll()

    const second = setup(server)
    await second.sessionManager.init()
    assert.ok(!(await second.nextMessage("ses_a")))
    assert.match(await second.nextMessage("ses_b"), /oc-ses_b/)
  })
})

test("no pointer when it is switched off, capture is off, or for a subagent session", async () => {
  await withOpenViking(async (server) => {
    for (const overrides of [{ sessionHistoryPointer: false }, { autoCapture: false }]) {
      const { sessionManager, nextMessage } = setup(server, overrides)
      await sessionManager.init()
      await sessionManager.handleEvent(compacted("ses_off"))
      assert.ok(!(await nextMessage("ses_off")), JSON.stringify(overrides))
    }

    const { sessionManager, nextMessage } = setup(server)
    await sessionManager.init()
    await sessionManager.handleEvent({
      type: "session.created",
      properties: { info: { id: "ses_child", parentID: "ses_parent" } },
    })
    await sessionManager.handleEvent(compacted("ses_child"))
    assert.ok(!(await nextMessage("ses_child")))
  })
})
