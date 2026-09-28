import test from "node:test"
import assert from "node:assert/strict"
import { contextMessageEvents, normalizeV2LifecycleEvent } from "../lib/v2-events.mjs"
import { extractPartsFromPayload } from "../lib/shared/capture-utils.mjs"

test("normalizeV2LifecycleEvent maps the v2 lifecycle onto the v1 handler shape", () => {
  const created = normalizeV2LifecycleEvent({
    type: "session.created",
    data: { sessionID: "ses_1", parentID: "ses_parent" },
  })
  assert.equal(created[0].type, "session.created")
  assert.equal(created[0].properties.info.id, "ses_1")
  assert.equal(created[0].properties.info.parentID, "ses_parent")

  assert.equal(normalizeV2LifecycleEvent({
    type: "session.execution.succeeded",
    data: { sessionID: "ses_1" },
  })[0].type, "session.idle")
  assert.equal(normalizeV2LifecycleEvent({
    type: "session.execution.failed",
    data: { sessionID: "ses_1", error: { message: "boom" } },
  })[0].type, "session.idle")
  assert.equal(normalizeV2LifecycleEvent({
    type: "session.compaction.ended",
    data: { sessionID: "ses_1" },
  })[0].type, "session.compacted")
  assert.deepEqual(normalizeV2LifecycleEvent({ type: "session.created", data: {} }), [])
})

test("contextMessageEvents converts v2 user and assistant messages", () => {
  const user = contextMessageEvents("ses_1", {
    id: "msg_user",
    type: "user",
    text: "remember this",
  })
  assert.equal(user[0].properties.info.role, "user")
  assert.equal(user[1].properties.part.text, "remember this")

  const assistant = contextMessageEvents("ses_1", {
    id: "msg_assistant",
    type: "assistant",
    content: [
      { type: "text", text: "answer" },
      {
        type: "tool",
        id: "call_1",
        name: "read",
        state: {
          status: "completed",
          input: { path: "README.md" },
          content: [{ type: "text", text: "file body" }],
        },
      },
    ],
  })
  assert.equal(assistant[0].properties.info.role, "assistant")
  assert.equal(assistant[1].properties.part.text, "answer")
  assert.equal(assistant[2].properties.part.type, "tool")
  assert.equal(assistant[2].properties.part.tool, "read")
  assert.equal(assistant[2].properties.part.state.content[0].text, "file body")
  assert.equal(extractPartsFromPayload(assistant[2].properties.part)[0].tool_output, "file body")
})

test("contextMessageEvents drops reasoning like the v1 capture path", () => {
  const events = contextMessageEvents("ses_1", {
    id: "msg_assistant",
    type: "assistant",
    content: [
      { type: "reasoning", text: "private chain of thought" },
      { type: "text", text: "final answer" },
    ],
  })
  const texts = events.slice(1).map((event) => event.properties.part.text)
  assert.deepEqual(texts, ["final answer"])
  assert.deepEqual(contextMessageEvents("ses_1", {
    id: "msg_reasoning_only",
    type: "assistant",
    content: [{ type: "reasoning", text: "thinking" }],
  }), [])
})

test("contextMessageEvents ignores non-conversation context entries", () => {
  assert.deepEqual(contextMessageEvents("ses_1", { id: "msg_idle", type: "idle" }), [])
  assert.deepEqual(contextMessageEvents("ses_1", { id: "msg_empty", type: "assistant", content: [] }), [])
})
