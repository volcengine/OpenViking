import test from "node:test"
import assert from "node:assert/strict"
import { createV2Recall } from "../lib/v2-recall.mjs"

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

function storageFixture(storage = new Map()) {
  return {
    storage,
    ctx: {
      storage: {
        get: async (key) => structuredClone(storage.get(key)),
        set: async (key, value) => { storage.set(key, structuredClone(value)) },
        remove: async (key) => { storage.delete(key) },
      },
    },
  }
}

function recallFixture({ ctx, config = {}, session, memories }) {
  const calls = { session: 0, recall: 0 }
  const v2Recall = createV2Recall({
    ctx,
    config: { recallWaitMs: 5000, ...config },
    directory: "/tmp/project",
    recallEnabled: true,
    sessionInject: {
      buildSessionContext: async (input) => {
        calls.session += 1
        return typeof session === "function" ? session(input) : session
      },
    },
    recall: {
      buildRelevantMemories: async (input, parts) => {
        calls.recall += 1
        return typeof memories === "function" ? memories(input, parts) : memories
      },
    },
  })
  return { v2Recall, calls }
}

function userMessage(id, text) {
  return { id, role: "user", content: [{ type: "text", text }] }
}

function contextEvent(...messages) {
  return { sessionID: "ses_1", system: [], messages: structuredClone(messages) }
}

test("prompt returns before recall settles and context injects it", async () => {
  const recall = deferred()
  const { ctx } = storageFixture()
  const { v2Recall, calls } = recallFixture({
    ctx,
    session: "<profile>profile</profile>",
    memories: (_input, parts) => {
      assert.equal(parts[0].text, "find the deployment notes")
      return recall.promise
    },
  })

  const returned = v2Recall.prefetch({ sessionID: "ses_1", messageID: "msg_1", prompt: { text: "find the deployment notes" } })
  assert.equal(returned, undefined)

  const event = contextEvent(userMessage("msg_1", "find the deployment notes"))
  const injecting = v2Recall.inject(event)
  recall.resolve("<openviking-context>memory</openviking-context>")
  await injecting

  const [message] = event.messages
  assert.equal(message.content.length, 2)
  assert.equal(message.content[0].text, "<profile>profile</profile>\n\n<openviking-context>memory</openviking-context>")
  assert.equal(message.content[0].metadata.openviking, true)
  assert.deepEqual(calls, { session: 1, recall: 1 })
})

test("later model requests replay the same bytes without another fetch", async () => {
  let turn = 0
  const { ctx } = storageFixture()
  const { v2Recall, calls } = recallFixture({
    ctx,
    session: undefined,
    memories: () => `<openviking-context>memory ${++turn}</openviking-context>`,
  })
  const first = userMessage("msg_1", "first question")
  const second = userMessage("msg_2", "second question")

  v2Recall.prefetch({ sessionID: "ses_1", messageID: "msg_1", prompt: { text: "first question" } })
  const step1 = contextEvent(first)
  await v2Recall.inject(step1)
  // A tool call inside the same turn sends the history again.
  const step2 = contextEvent(first)
  await v2Recall.inject(step2)
  v2Recall.prefetch({ sessionID: "ses_1", messageID: "msg_2", prompt: { text: "second question" } })
  const step3 = contextEvent(first, second)
  await v2Recall.inject(step3)

  assert.deepEqual(step2.messages[0], step1.messages[0])
  assert.deepEqual(step3.messages[0], step1.messages[0])
  assert.match(step3.messages[1].content[0].text, /memory 2/)
  assert.equal(calls.recall, 2)
})

test("recall that misses the budget is skipped and the message stays stable", async () => {
  const late = deferred()
  const { ctx } = storageFixture()
  const { v2Recall } = recallFixture({ ctx, config: { recallWaitMs: 20 }, memories: () => late.promise })
  const message = userMessage("msg_1", "question")

  v2Recall.prefetch({ sessionID: "ses_1", messageID: "msg_1", prompt: { text: "question" } })
  const first = contextEvent(message)
  await v2Recall.inject(first)
  late.resolve("<openviking-context>too late</openviking-context>")
  await new Promise((resolve) => setImmediate(resolve))
  const second = contextEvent(message)
  await v2Recall.inject(second)

  assert.deepEqual(first.messages[0], message)
  assert.deepEqual(second.messages[0], message)
})

test("session-start context that arrives late rides on the next message", async () => {
  const late = deferred()
  const { ctx } = storageFixture()
  let sessionCalls = 0
  const { v2Recall } = recallFixture({
    ctx,
    config: { recallWaitMs: 20 },
    session: () => (++sessionCalls === 1 ? late.promise : undefined),
    memories: undefined,
  })
  const first = userMessage("msg_1", "first")
  const second = userMessage("msg_2", "second")

  v2Recall.prefetch({ sessionID: "ses_1", messageID: "msg_1", prompt: { text: "first" } })
  const turn1 = contextEvent(first)
  await v2Recall.inject(turn1)
  late.resolve("<profile>profile</profile>")
  await new Promise((resolve) => setImmediate(resolve))

  v2Recall.prefetch({ sessionID: "ses_1", messageID: "msg_2", prompt: { text: "second" } })
  const turn2 = contextEvent(first, second)
  await v2Recall.inject(turn2)

  assert.deepEqual(turn2.messages[0], first)
  assert.equal(turn2.messages[1].content[0].text, "<profile>profile</profile>")
})

test("a new plugin instance replays the stored ledger and deletion clears it", async () => {
  const { ctx, storage } = storageFixture()
  const first = recallFixture({ ctx, memories: "<openviking-context>memory</openviking-context>" })
  const message = userMessage("msg_1", "question")

  first.v2Recall.prefetch({ sessionID: "ses_1", messageID: "msg_1", prompt: { text: "question" } })
  const before = contextEvent(message)
  await first.v2Recall.inject(before)
  assert.equal(storage.size, 1)

  const restarted = recallFixture({ ctx, memories: "<openviking-context>different</openviking-context>" })
  const after = contextEvent(message)
  await restarted.v2Recall.inject(after)
  assert.deepEqual(after.messages[0], before.messages[0])
  assert.equal(restarted.calls.recall, 0)

  await restarted.v2Recall.forget("ses_1")
  assert.equal(storage.size, 0)
})

test("recallLedger=false keeps the ledger in memory only", async () => {
  const { ctx, storage } = storageFixture()
  const { v2Recall } = recallFixture({ ctx, config: { recallLedger: false }, memories: "<openviking-context>memory</openviking-context>" })
  const message = userMessage("msg_1", "question")

  v2Recall.prefetch({ sessionID: "ses_1", messageID: "msg_1", prompt: { text: "question" } })
  const first = contextEvent(message)
  await v2Recall.inject(first)
  const second = contextEvent(message)
  await v2Recall.inject(second)

  assert.deepEqual(second.messages[0], first.messages[0])
  assert.equal(storage.size, 0)
})

test("blocks persisted in prompt metadata by earlier releases are still injected", async () => {
  const { ctx } = storageFixture()
  const { v2Recall } = recallFixture({ ctx })
  const message = {
    ...userMessage("msg_old", "question"),
    metadata: { openviking: { context: ["<profile>profile</profile>", "<openviking-context>memory</openviking-context>"] } },
  }
  const event = contextEvent(message)
  await v2Recall.inject(event)
  await v2Recall.inject(event)

  assert.equal(event.messages[0].content.length, 2)
  assert.equal(event.messages[0].content[0].text, "<profile>profile</profile>\n\n<openviking-context>memory</openviking-context>")
})

test("fetch failures inject nothing and never reject", async () => {
  const { ctx } = storageFixture()
  const { v2Recall } = recallFixture({
    ctx,
    session: () => { throw new Error("profile down") },
    memories: () => { throw new Error("recall down") },
  })
  const message = userMessage("msg_1", "question")

  v2Recall.prefetch({ sessionID: "ses_1", messageID: "msg_1", prompt: { text: "question" } })
  const event = contextEvent(message)
  await assert.doesNotReject(() => v2Recall.inject(event))
  assert.deepEqual(event.messages[0], message)
})
