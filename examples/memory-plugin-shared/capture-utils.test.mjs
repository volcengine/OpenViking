import test from "node:test"
import assert from "node:assert/strict"
import {
  extractCaptureTurns,
  extractPartsFromPayload,
  filterCaptureParts,
  shouldCaptureText,
} from "./lib/capture-utils.mjs"

function toolPart(parts) {
  return parts.find((part) => part?.type === "tool")
}

test("toolStatus marks a camelCase isError result as error", () => {
  const parts = extractPartsFromPayload({
    role: "user",
    content: [{
      type: "tool-result",
      toolCallId: "call-1",
      content: [{ type: "text", text: "bash: df: command not found" }],
      isError: true,
    }],
  }, { toolNameById: { "call-1": "bash" } })
  assert.equal(toolPart(parts).tool_status, "error")
})

test("toolStatus keeps snake_case is_error support", () => {
  const parts = extractPartsFromPayload({
    role: "user",
    content: [{
      type: "tool_result",
      tool_use_id: "call-2",
      is_error: true,
      output: "connection refused",
    }],
  }, { toolNameById: { "call-2": "mysqladmin" } })
  assert.equal(toolPart(parts).tool_status, "error")
})

test("toolStatus recognizes a camelCase isError nested under state", () => {
  const parts = extractPartsFromPayload({
    role: "user",
    content: [{
      type: "tool-result",
      toolCallId: "call-2b",
      state: { isError: true },
      output: "connection refused",
    }],
  }, { toolNameById: { "call-2b": "mysqladmin" } })
  assert.equal(toolPart(parts).tool_status, "error")
})

test("toolStatus stays completed for a successful camelCase result", () => {
  const parts = extractPartsFromPayload({
    role: "user",
    content: [{
      type: "tool-result",
      toolCallId: "call-3",
      content: [{ type: "text", text: "ok" }],
      isError: false,
    }],
  }, { toolNameById: { "call-3": "bash" } })
  assert.equal(toolPart(parts).tool_status, "completed")
})

const TOOL_PART = { type: "tool", tool_name: "bash", tool_input: "ls" }

function userEntry(text) {
  return { payload: { role: "user", content: [{ type: "text", text }] } }
}

test("filterCaptureParts prunes blank text parts even with no rules configured", () => {
  const shaped = filterCaptureParts(
    [{ type: "text", text: "  " }, { type: "text", text: " kept " }, TOOL_PART],
    "user",
    {},
  )
  assert.equal(shaped.dropped, false)
  assert.deepEqual(shaped.parts, [{ type: "text", text: "kept" }, TOOL_PART])
})

test("filterCaptureParts substitutes in every text part and leaves tool parts alone", () => {
  const shaped = filterCaptureParts(
    [{ type: "text", text: "token sk_ABCDEF here" }, TOOL_PART, { type: "text", text: "and sk_ZZZ" }],
    "user",
    { captureFilters: ["s/sk_[A-Za-z0-9]+/[redacted]/g"] },
  )
  assert.equal(shaped.dropped, false)
  assert.deepEqual(shaped.parts, [
    { type: "text", text: "token [redacted] here" },
    TOOL_PART,
    { type: "text", text: "and [redacted]" },
  ])
})

test("filterCaptureParts takes one drop verdict on the aggregate, tool parts included", () => {
  const cfg = { captureFilters: ["d/^internal notes/"] }
  const shaped = filterCaptureParts(
    [{ type: "text", text: "internal notes" }, TOOL_PART, { type: "text", text: "trailing" }],
    "user",
    cfg,
  )
  assert.equal(shaped.dropped, true)
  assert.deepEqual(shaped.parts, [])

  // The aggregate is what a keep-only rule sees, so a match anywhere keeps the turn.
  const keep = filterCaptureParts(
    [{ type: "text", text: "one" }, { type: "text", text: "keeper" }],
    "user",
    { captureFilters: ["k/keeper/"] },
  )
  assert.equal(keep.dropped, false)
  assert.equal(keep.parts.length, 2)
})

test("filterCaptureParts never judges a turn that carries no text", () => {
  const shaped = filterCaptureParts([TOOL_PART], "user", { captureFilters: ["k/never matches/"] })
  assert.equal(shaped.dropped, false)
  assert.deepEqual(shaped.parts, [TOOL_PART])
  assert.equal(filterCaptureParts([], "user", { captureFilters: ["k/x/"] }).dropped, false)
})

test("shouldCaptureText reports a filtered drop and can be asked to skip filters", () => {
  const cfg = { captureFilters: ["user:d/^\\/compact\\b/", "s/^ultrathink\\s+//"] }
  assert.deepEqual(shouldCaptureText("/compact the thread", "user", cfg), {
    shouldCapture: false,
    reason: "filtered",
    text: "",
  })
  assert.equal(shouldCaptureText("/compact the thread", "assistant", cfg).shouldCapture, true)
  assert.equal(shouldCaptureText("ultrathink about the retry policy", "user", cfg).text,
    "about the retry policy")
  assert.equal(
    shouldCaptureText("ultrathink about the retry policy", "user", cfg, { filters: false }).text,
    "ultrathink about the retry policy",
  )
})

test("extractCaptureTurns drops filtered turns and keeps turn.text unfiltered", () => {
  const entries = [userEntry("please remember the retry policy"), userEntry("scratch: throwaway note")]
  const cfg = { captureFilters: ["d/^scratch:/", "s/^please\\s+//"] }
  const turns = extractCaptureTurns(entries, cfg)
  assert.equal(turns.length, 1)
  assert.deepEqual(turns[0].parts, [{ type: "text", text: "remember the retry policy" }])
  // parts are on the wire, so the text path stayed faithful for keyword scanning
  assert.equal(turns[0].text, "please remember the retry policy")
})

test("extractCaptureTurns falls back to the text path when a turn has no parts", () => {
  const entries = [{ payload: { role: "user", content: [{ type: "unknown", value: 1 }], text: "" } }]
  assert.deepEqual(extractCaptureTurns(entries, { captureFilters: ["d/x/"] }), [])
})

test("extractCaptureTurns honours the role scope of a capture rule", () => {
  const cfg = { captureAssistantTurns: true, captureFilters: ["assistant:d/^ok so/"] }
  const entries = [
    userEntry("ok so what did we decide"),
    { payload: { role: "assistant", content: [{ type: "text", text: "ok so here is the plan" }] } },
  ]
  const turns = extractCaptureTurns(entries, cfg)
  assert.deepEqual(turns.map((turn) => turn.role), ["user"])
})
