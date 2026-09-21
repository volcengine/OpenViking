import test from "node:test"
import assert from "node:assert/strict"
import { startV2Plugin } from "../lib/v2-plugin.mjs"

test("startV2Plugin registers the v2 MCP server and captures an admitted prompt", async () => {
  const events = []
  const registered = []
  const runtime = {
    config: { mcp: { enabled: true } },
    sessionManager: {
      handleEvent: async (event) => {
        events.push(event)
      },
      flushSession: async () => {},
      flushAll: async () => {},
    },
    repoContext: {
      getRepoSystemPrompt: () => null,
      refreshRepos: async () => {},
    },
    recall: { injectRelevantMemories: async () => {} },
    sessionInject: { injectSessionContext: async () => {} },
    vikingUriGuard: async () => {},
    vikingUriNotice: async () => {},
  }
  const hooks = {}
  const ctx = {
    location: { directory: "/tmp/project" },
    mcp: {
      transform: async (callback) => {
        callback({
          get: () => undefined,
          set: (name, config) => registered.push([name, config]),
        })
      },
    },
    tool: {
      hook: async (name, fn) => {
        hooks[name] = fn
      },
    },
    session: {
      hook: async (name, fn) => {
        hooks[name] = fn
      },
    },
    event: {
      subscribe() {
        return (async function* empty() {})()
      },
    },
  }

  const cleanup = await startV2Plugin(ctx, runtime, { pluginRoot: "/tmp/ov" })
  assert.equal(registered[0][0], "openviking")
  assert.equal(registered[0][1].type, "local")
  assert.equal(registered[0][1].disabled, undefined)

  await hooks.prompt({
    sessionID: "ses_1",
    messageID: "msg_1",
    prompt: { text: "remember this" },
  })
  assert.equal(events[0].properties.info.role, "user")
  assert.equal(events[1].properties.part.text, "remember this")
  await cleanup()
})
