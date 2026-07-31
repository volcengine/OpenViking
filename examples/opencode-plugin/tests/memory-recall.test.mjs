import test from "node:test"
import assert from "node:assert/strict"
import { extractCurrentUserText } from "../lib/memory-recall.mjs"

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
