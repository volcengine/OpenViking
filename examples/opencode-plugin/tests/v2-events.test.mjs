import test from "node:test"
import assert from "node:assert/strict"
import { normalizeV2Event, userPromptEvents } from "../lib/v2-events.mjs"
import { latestUserText } from "../lib/v2-plugin.mjs"

test("normalizeV2Event maps session lifecycle onto the v1 handler shape", () => {
  const created = normalizeV2Event({
    type: "session.created",
    data: { sessionID: "ses_1", parentID: "ses_parent" },
  })
  assert.equal(created[0].type, "session.created")
  assert.equal(created[0].properties.info.id, "ses_1")
  assert.equal(created[0].properties.info.parentID, "ses_parent")

  assert.equal(normalizeV2Event({
    type: "session.execution.succeeded",
    data: { sessionID: "ses_1" },
  })[0].type, "session.idle")
  assert.equal(normalizeV2Event({
    type: "session.execution.failed",
    data: { sessionID: "ses_1", message: "boom" },
  })[0].type, "session.error")
  assert.deepEqual(normalizeV2Event({ type: "session.created", data: {} }), [])
})

test("normalizeV2Event turns message content into capture parts", () => {
  const events = normalizeV2Event({
    type: "session.message.content.updated",
    data: {
      sessionID: "ses_1",
      messageID: "msg_1",
      content: [
        { type: "text", text: "answer" },
        {
          type: "tool",
          id: "call_1",
          name: "read",
          state: { status: "completed", content: [{ type: "text", text: "file body" }] },
        },
      ],
    },
  })

  assert.equal(events[0].properties.info.role, "assistant")
  assert.equal(events[1].properties.part.text, "answer")
  assert.equal(events[2].properties.part.type, "tool")
  assert.equal(events[2].properties.part.name, "read")
  assert.equal(events[2].properties.part.output, "file body")
})

test("userPromptEvents captures the admitted prompt without requiring a v2 content event", () => {
  const events = userPromptEvents({ sessionID: "ses_1", messageID: "msg_user", text: "remember this" })
  assert.equal(events[0].properties.info.role, "user")
  assert.equal(events[1].properties.part.text, "remember this")
  assert.deepEqual(userPromptEvents({ sessionID: "ses_1", messageID: "msg_user", text: "  " }), [])
})

test("latestUserText reads the newest user message", () => {
  assert.equal(latestUserText([
    { role: "user", content: "older" },
    { role: "assistant", content: [{ type: "text", text: "reply" }] },
    { role: "user", content: [{ type: "text", text: "newest" }] },
  ]), "newest")
})
