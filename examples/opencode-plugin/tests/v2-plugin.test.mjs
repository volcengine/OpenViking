import test from "node:test"
import assert from "node:assert/strict"
import { captureV2Context, injectV2Context, prepareV2Prompt, startV2Plugin } from "../lib/v2-plugin.mjs"
import { createVikingUriNotice } from "../lib/viking-uri-guard.mjs"

function runtimeFixture(overrides = {}) {
  const events = []
  return {
    events,
    runtime: {
      config: { mcp: { enabled: true }, recall: { enabled: true } },
      sessionManager: {
        handleEvent: async (event) => events.push(event),
        flushSession: async () => {},
        flushAll: async () => {},
      },
      repoContext: {
        getRepoSystemPrompt: () => null,
        refreshRepos: async () => {},
      },
      recall: { buildRelevantMemories: async () => undefined },
      sessionInject: { buildSessionContext: async () => undefined },
      vikingUriGuard: async () => {},
      vikingUriNotice: async () => {},
      ready: Promise.resolve(),
      ...overrides,
    },
  }
}

function contextFixture(messages = []) {
  const hooks = {}
  const registered = []
  return {
    hooks,
    registered,
    ctx: {
      location: { directory: "/tmp/project" },
      mcp: {
        transform: async (callback) => callback({
          get: () => undefined,
          set: (name, config) => registered.push([name, config]),
        }),
      },
      tool: { hook: async (name, fn) => { hooks[name] = fn } },
      session: {
        hook: async (name, fn) => { hooks[name] = fn },
        context: async () => messages,
      },
      event: { subscribe: () => (async function* empty() {})() },
    },
  }
}

test("startV2Plugin registers direct MCP tools and all v2 hooks", async () => {
  const { runtime } = runtimeFixture()
  const { ctx, hooks, registered } = contextFixture()
  const cleanup = await startV2Plugin(ctx, runtime, { pluginRoot: "/tmp/ov" })

  assert.equal(registered[0][0], "openviking")
  assert.equal(registered[0][1].type, "local")
  assert.equal(registered[0][1].codemode, false)
  assert.deepEqual(Object.keys(hooks).sort(), ["compaction", "context", "execute.after", "execute.before", "prompt"])
  await cleanup()
})

test("compaction captures messages before the host drops them and contains read failures", async () => {
  const { runtime, events } = runtimeFixture()
  const messages = [
    { id: "user", type: "user", text: "run the build" },
    { id: "assistant", type: "assistant", content: [
      { type: "tool", id: "build", name: "shell", state: { status: "completed", input: { command: "make" }, content: [{ type: "text", text: "built" }] } },
    ] },
  ]
  const { ctx, hooks } = contextFixture(messages)
  const cleanup = await startV2Plugin(ctx, runtime, { pluginRoot: "/tmp/ov" })
  await hooks.compaction({ sessionID: "ses_1" })
  assert.equal(events.filter((event) => event.type === "message.updated").length, 2)
  assert.equal(events.at(-1).properties.part.output, "built")
  messages.length = 0
  await hooks.compaction({ sessionID: "ses_1" })
  assert.equal(events.filter((event) => event.type === "message.updated").length, 2)
  ctx.session.context = async () => { throw new Error("host unavailable") }
  await assert.doesNotReject(() => hooks.compaction({ sessionID: "ses_1" }))
  await cleanup()
})

test("shell notice preserves multiple text and media blocks without duplicating output", async () => {
  const { runtime } = runtimeFixture({ vikingUriNotice: createVikingUriNotice() })
  const { ctx, hooks } = contextFixture()
  const cleanup = await startV2Plugin(ctx, runtime, { pluginRoot: "/tmp/ov" })
  const original = [
    { type: "text", text: "command output", metadata: { source: "stdout" } },
    { type: "image", data: "test" },
    { type: "text", text: "Exited with code 1" },
  ]
  const result = { content: structuredClone(original) }
  await hooks["execute.after"]({ status: "completed", tool: "shell", input: { command: "cat viking://resources/test" }, result })
  assert.deepEqual(result.content.slice(0, 3), original)
  assert.equal(result.content.length, 4)
  assert.match(result.content[3].text, /openviking_read/)
  assert.doesNotMatch(result.content[3].text, /Exited with code 1/)
  await cleanup()
})

test("cleanup waits for background initialization before flushing", async () => {
  let completeBackground
  let flushed = false
  const background = new Promise((resolve) => { completeBackground = resolve })
  const { runtime } = runtimeFixture({ background })
  runtime.sessionManager.flushAll = async () => { flushed = true }
  const { ctx } = contextFixture()
  const cleanup = await startV2Plugin(ctx, runtime, { pluginRoot: "/tmp/ov" })
  const cleaning = cleanup()
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(flushed, false)
  completeBackground()
  await cleaning
  assert.equal(flushed, true)
})

test("prompt metadata is persisted and context injection is stable across model steps", async () => {
  let recallCalls = 0
  const event = {
    sessionID: "ses_1",
    messageID: "msg_1",
    prompt: { text: "find the deployment notes" },
    metadata: { caller: "test" },
  }
  await prepareV2Prompt(event, {
    directory: "/tmp/project",
    recallEnabled: true,
    sessionInject: { buildSessionContext: async () => "<profile>profile</profile>" },
    recall: {
      buildRelevantMemories: async (_input, parts) => {
        recallCalls += 1
        assert.equal(parts[0].text, "find the deployment notes")
        return "<openviking-context>memory</openviking-context>"
      },
    },
  })
  assert.equal(recallCalls, 1)
  assert.equal(event.metadata.caller, "test")

  const message = { role: "user", content: [{ type: "text", text: event.prompt.text }], metadata: event.metadata }
  const context = { system: [], messages: [message] }
  const repoContext = { getRepoSystemPrompt: () => "repo prompt" }
  injectV2Context(context, repoContext)
  injectV2Context(context, repoContext)

  assert.equal(message.content.length, 2)
  assert.match(message.content[0].text, /<profile>profile<\/profile>/)
  assert.match(message.content[0].text, /<openviking-context>memory<\/openviking-context>/)
  assert.equal(message.content[0].metadata.openviking, true)
  assert.equal(context.system.length, 1)
})

test("captureV2Context advances a per-session cursor and preserves tool parts", async () => {
  const events = []
  const messages = [
    { id: "msg_user", type: "user", text: "question" },
    {
      id: "msg_assistant",
      type: "assistant",
      content: [
        { type: "text", text: "answer" },
        {
          type: "tool", id: "call_1", name: "read",
          state: { status: "completed", input: { path: "README.md" }, content: [{ type: "text", text: "body" }] },
        },
      ],
    },
  ]
  const ctx = { session: { context: async () => messages } }
  const manager = { handleEvent: async (event) => events.push(event) }
  const cursors = new Map()

  await captureV2Context(ctx, manager, cursors, "ses_1")
  assert.equal(events.filter((event) => event.type === "message.updated").length, 2)
  assert.equal(events.at(-1).properties.part.tool, "read")
  assert.equal(cursors.get("ses_1"), "msg_assistant")

  events.length = 0
  await captureV2Context(ctx, manager, cursors, "ses_1")
  assert.deepEqual(events, [])
})

test("v2 prompt, context, after-tool, and event failures are contained", async () => {
  const { runtime } = runtimeFixture({
    recall: { buildRelevantMemories: async () => { throw new Error("recall failed") } },
    vikingUriNotice: async () => { throw new Error("notice failed") },
  })
  const { ctx, hooks } = contextFixture()
  const cleanup = await startV2Plugin(ctx, runtime, { pluginRoot: "/tmp/ov" })

  const prompt = { sessionID: "ses_1", messageID: "msg_1", prompt: { text: "query" } }
  runtime.sessionInject.buildSessionContext = async () => "<profile>still available</profile>"
  await assert.doesNotReject(() => hooks.prompt(prompt))
  assert.equal(prompt.metadata.openviking.context[0], "<profile>still available</profile>")
  await assert.doesNotReject(() => hooks.context({ sessionID: "ses_1", messages: null, system: [] }))
  await assert.doesNotReject(() => hooks["execute.after"]({
    status: "completed", tool: "shell", input: { command: "ls" }, result: { content: [] },
  }))
  await cleanup()
})

function eventLoopFixture({ events, contexts, locations, storage = new Map() }) {
  const contextCalls = []
  return {
    contextCalls,
    storage,
    ctx: {
      location: { directory: "/repo/a" },
      session: {
        hook: async () => {},
        context: async ({ sessionID }) => {
          contextCalls.push(sessionID)
          return contexts[sessionID] ?? []
        },
        get: async ({ sessionID }) => ({ data: { id: sessionID, location: { directory: locations[sessionID] } } }),
      },
      storage: {
        get: async (key) => storage.get(key),
        set: async (key, value) => { storage.set(key, value) },
        remove: async (key) => { storage.delete(key) },
      },
      event: { subscribe: () => (async function* replay() { yield* events })() },
    },
  }
}

const conversation = [
  { id: "msg_user", type: "user", text: "question" },
  { id: "msg_assistant", type: "assistant", content: [{ type: "text", text: "answer" }] },
]

test("event loop handles only sessions that belong to this location", async () => {
  const { runtime, events: handled } = runtimeFixture()
  const { ctx, contextCalls } = eventLoopFixture({
    events: [
      { type: "session.created", data: { sessionID: "ses_a", location: { directory: "/repo/a" } } },
      { type: "session.created", data: { sessionID: "ses_b", location: { directory: "/repo/b" } } },
      { type: "session.execution.succeeded", data: { sessionID: "ses_a" } },
      { type: "session.execution.succeeded", data: { sessionID: "ses_b" } },
      { type: "session.execution.succeeded", data: { sessionID: "ses_c" } },
      { type: "session.execution.failed", data: { sessionID: "ses_a", error: { message: "rate limited" } } },
    ],
    contexts: { ses_a: conversation, ses_b: conversation, ses_c: conversation },
    locations: { ses_c: "/repo/b" },
  })
  const cleanup = await startV2Plugin(ctx, runtime, { pluginRoot: "/tmp/ov" })
  await cleanup()

  assert.deepEqual(contextCalls, ["ses_a", "ses_a"])
  const sessionOf = (event) => event.properties.info?.sessionID ?? event.properties.part?.sessionID
  assert.ok(handled.every((event) => sessionOf(event) === "ses_a"))
  assert.deepEqual(
    handled.filter((event) => event.type.startsWith("session.")).map((event) => event.type),
    ["session.created", "session.idle", "session.idle"],
  )
  assert.equal(handled.filter((event) => event.type === "message.updated").length, 2)
})

test("a new plugin instance resumes from the stored capture cursor", async () => {
  const storage = new Map()
  const first = runtimeFixture()
  const firstLoop = eventLoopFixture({
    events: [{ type: "session.execution.succeeded", data: { sessionID: "ses_a" } }],
    contexts: { ses_a: conversation },
    locations: { ses_a: "/repo/a" },
    storage,
  })
  await (await startV2Plugin(firstLoop.ctx, first.runtime, { pluginRoot: "/tmp/ov" }))()
  assert.equal(first.events.filter((event) => event.type === "message.updated").length, 2)

  const second = runtimeFixture()
  const followUp = { id: "msg_follow_up", type: "user", text: "next question" }
  const secondLoop = eventLoopFixture({
    events: [
      { type: "session.execution.succeeded", data: { sessionID: "ses_a" } },
      { type: "session.deleted", data: { sessionID: "ses_a" } },
    ],
    contexts: { ses_a: [...conversation, followUp] },
    locations: { ses_a: "/repo/a" },
    storage,
  })
  await (await startV2Plugin(secondLoop.ctx, second.runtime, { pluginRoot: "/tmp/ov" }))()

  const captured = second.events.filter((event) => event.type === "message.updated")
  assert.deepEqual(captured.map((event) => event.properties.info.id), ["msg_follow_up"])
  assert.equal(storage.size, 0)
})
