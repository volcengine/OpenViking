import test from "node:test"
import assert from "node:assert/strict"
import { createMemoryRecall, extractCurrentUserText } from "../lib/memory-recall.mjs"

test("an unreachable server is reported in the session, not skipped silently", async () => {
  const notices = []
  const recall = createMemoryRecall({
    // Port 1 on loopback refuses the connection: a real outage, no mock.
    config: { endpoint: "http://127.0.0.1:1", minQueryLength: 3, timeoutMs: 2000 },
    sessionManager: { getMappedSessionId: () => "" },
    notify: async (message, variant) => { notices.push({ message, variant }) },
  })

  const block = await recall.buildRelevantMemories(
    { sessionID: "ses-outage" },
    [{ type: "text", text: "What deploy rules apply to this repository?" }],
  )

  assert.equal(block, undefined)
  assert.equal(notices.length, 1)
  assert.equal(notices[0].variant, "error")
  assert.match(notices[0].message, /OpenViking memory is unavailable at http:\/\/127\.0\.0\.1:1/)
})

test("extractCurrentUserText skips synthetic session context before user text", () => {
  const query = extractCurrentUserText([
    {
      type: "text",
      text: "<openviking-context source=\"session-start\">Profile</openviking-context>",
      synthetic: true,
    },
    {
      type: "text",
      text: "Find the deployment notes for the recall test",
    },
  ])

  assert.equal(query, "Find the deployment notes for the recall test")
})

test("extractCurrentUserText still rejects non-synthetic OpenViking context text", () => {
  const query = extractCurrentUserText([
    {
      type: "text",
      text: "<openviking-context>Injected context</openviking-context>",
    },
    {
      type: "text",
      text: "Find the deployment notes for the recall test",
    },
  ])

  assert.equal(query, null)
})
