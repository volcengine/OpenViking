import test from "node:test"
import assert from "node:assert/strict"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { createMemorySessionManager } from "../lib/memory-session.mjs"
import { fillHistorySession } from "../lib/session-history.mjs"
import { startV2Plugin } from "../lib/v2-plugin.mjs"

async function withSessionManager(fn) {
  const dir = await mkdtemp(join(tmpdir(), "ov-oc-history-"))
  try {
    const sessionManager = createMemorySessionManager({
      config: { endpoint: "http://127.0.0.1:9", timeoutMs: 1000, autoCapture: true, commitTokenThreshold: 20000 },
      pluginRoot: dir,
    })
    await sessionManager.init({ deferNetwork: true })
    return await fn(sessionManager)
  } finally {
    await rm(dir, { recursive: true, force: true })
  }
}

test("a session_history call without session gets the calling session's OV id", async () => {
  await withSessionManager(async (sessionManager) => {
    await sessionManager.handleEvent({
      type: "session.created",
      properties: { info: { id: "ses_child", parentID: "ses_main" } },
    })
    for (const session of [undefined, null, "", "  "]) {
      const args = { action: "search", query: "error", session }
      fillHistorySession(sessionManager, { tool: "openviking_session_history", sessionID: "ses_main", args })
      assert.equal(args.session, "oc-ses_main", String(session))
    }
    const child = { action: "overview" }
    fillHistorySession(sessionManager, { tool: "openviking_session_history", sessionID: "ses_child", args: child })
    assert.equal(child.session, sessionManager.getMappedSessionId("ses_child"))
    assert.match(child.session, /__subagent-/)
  })
})

test("a chosen session, another tool, or another MCP server is left alone", async () => {
  await withSessionManager(async (sessionManager) => {
    const cases = [
      ["openviking_session_history", { session: "oc-ses_other" }],
      ["openviking_session_history", { session: ["oc-a"] }],
      ["openviking_search", { query: "x" }],
      ["other_session_history", { action: "overview" }],
    ]
    for (const [tool, args] of cases) {
      const before = structuredClone(args)
      fillHistorySession(sessionManager, { tool, sessionID: "ses_main", args })
      assert.deepEqual(args, before, tool)
    }
    assert.doesNotThrow(() => fillHistorySession(sessionManager, {
      tool: "openviking_session_history", sessionID: "ses_main", args: undefined,
    }))
  })
})

test("the v2 execute.before hook fills session in the tool input", async () => {
  const hooks = {}
  const guarded = []
  const runtime = {
    config: { mcp: { enabled: false } },
    sessionManager: { getMappedSessionId: (id) => `oc-${id}`, flushAll: async () => {} },
    repoContext: {},
    recall: {},
    sessionInject: {},
    vikingUriGuard: async (input) => guarded.push(structuredClone(input)),
    vikingUriNotice: async () => {},
    ready: Promise.resolve(),
  }
  const ctx = { tool: { hook: async (name, fn) => { hooks[name] = fn } } }
  const cleanup = await startV2Plugin(ctx, runtime, { pluginRoot: "/nonexistent" })
  const event = { tool: "openviking_session_history", sessionID: "ses_v2", input: { action: "overview" } }
  await hooks["execute.before"](event)
  assert.deepEqual(event.input, { action: "overview", session: "oc-ses_v2" })
  assert.equal(guarded[0].args.session, "oc-ses_v2")
  await cleanup()
})
