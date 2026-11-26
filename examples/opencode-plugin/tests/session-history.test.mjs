import test from "node:test"
import assert from "node:assert/strict"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { createMemorySessionManager } from "../lib/memory-session.mjs"
import { createSessionInject, keepNewestStartContext } from "../lib/session-inject.mjs"
import { startV2Plugin } from "../lib/v2-plugin.mjs"

test("the v2 execute.before hook fills a missing session and keeps a chosen one", async () => {
  const hooks = {}
  const runtime = {
    config: { mcp: { enabled: false } },
    sessionManager: { getMappedSessionId: (id) => `oc-${id}`, flushAll: async () => {} },
    vikingUriGuard: async () => {},
    ready: Promise.resolve(),
  }
  const ctx = { tool: { hook: async (name, fn) => { hooks[name] = fn } } }
  const cleanup = await startV2Plugin(ctx, runtime, { pluginRoot: "/nonexistent" })
  const missing = { tool: "openviking_session_history", sessionID: "ses_v2", input: { action: "overview" } }
  const chosen = { tool: "openviking_session_history", sessionID: "ses_v2", input: { session: "oc-ses_other" } }
  await hooks["execute.before"](missing)
  await hooks["execute.before"](chosen)
  assert.equal(missing.input.session, "oc-ses_v2")
  assert.equal(chosen.input.session, "oc-ses_other")
  await cleanup()
})

test("the first message after a compaction brings the start context back, with the pointer unless it is off", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-oc-pointer-"))
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (url) => Response.json({ status: "ok", result: String(url).includes("/content/read") ? "Prefers tabs" : [] })
  try {
    for (const sessionHistoryPointer of [true, false]) {
      const config = {
        endpoint: "http://127.0.0.1:9",
        timeoutMs: 1000,
        autoCapture: true,
        profileTokenBudget: 1000,
        sessionHistoryPointer,
        mcp: { enabled: true },
        commitTokenThreshold: 20000,
      }
      const sessionManager = createMemorySessionManager({ config, pluginRoot: await mkdtemp(join(dir, "run-")) })
      const inject = createSessionInject({ config, sessionManager })
      await sessionManager.init({ deferNetwork: true })
      await sessionManager.handleEvent({ type: "session.created", properties: { info: { id: "ses_main" } } })
      assert.match(await inject.buildSessionContext({ sessionID: "ses_main", messageID: "msg-0" }), /Prefers tabs/)
      assert.ok(!(await inject.buildSessionContext({ sessionID: "ses_main", messageID: "msg-1" })))
      await sessionManager.handleEvent({ type: "session.compacted", sessionID: "ses_main" })
      const first = await inject.buildSessionContext({ sessionID: "ses_main", messageID: "msg-2" })
      assert.match(first, /Prefers tabs/)
      if (sessionHistoryPointer) assert.match(first, /full record of this session as oc-ses_main/)
      else assert.doesNotMatch(first, /full record of this session/)
      assert.ok(!(await inject.buildSessionContext({ sessionID: "ses_main", messageID: "msg-3" })))
      await sessionManager.flushAll()
    }
  } finally {
    globalThis.fetch = originalFetch
    await rm(dir, { recursive: true, force: true })
  }
})

test("a request keeps only the newest start context a compaction left in view", () => {
  const block = (text) => `<openviking-context source="session-start">\n${text}\n</openviking-context>`
  const pointer = '<openviking-context source="session-history">\nnote\n</openviking-context>'
  const tail = [{ type: "text", text: block("old") }, { type: "text", text: "kept prompt" }]
  const replayed = [{ type: "text", text: `${block("older")}\n${pointer}` }]
  const latest = [{ type: "text", text: block("new") }]
  keepNewestStartContext([tail, replayed, latest, undefined])
  assert.deepEqual(tail.map((part) => part.text), ["kept prompt"])
  assert.deepEqual(replayed.map((part) => part.text), [pointer])
  assert.equal(latest[0].text, block("new"))
})
