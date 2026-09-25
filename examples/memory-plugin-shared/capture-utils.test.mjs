import test from "node:test"
import assert from "node:assert/strict"
import {
  collectToolNamesByIdFromEntries,
  extractCaptureTurns,
  extractPartsFromPayload,
  findLastHumanTurnIndex,
  filterCaptureParts,
  sanitizeCapturedText,
  shapeCapturePayload,
  shapeCaptureParts,
  shouldCaptureText,
} from "./lib/capture-utils.mjs"

const CAPTURE_CONFIG = {
  captureAssistantTurns: true,
  captureToolMaxChars: 1000000,
  captureMaxLength: 24000,
}

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

test("a tool result is labelled with the name of the call it answers", () => {
  const entries = [
    { role: "assistant", content: [{ type: "tool_use", id: "toolu_1", name: "Read", input: {} }] },
    { role: "user", content: [{ type: "tool_result", tool_use_id: "toolu_1", output: "ok" }] },
  ]
  const parts = extractPartsFromPayload(entries[1], {
    toolNameById: collectToolNamesByIdFromEntries(entries),
  })
  assert.equal(toolPart(parts).tool_name, "Read")
})

test("a host system reminder is not part of the conversation", () => {
  assert.equal(
    sanitizeCapturedText("before\n<system-reminder>never store this</system-reminder>\nafter"),
    "before\n\nafter",
  )
})

test("a subagent context line is not part of the conversation", () => {
  assert.equal(
    sanitizeCapturedText("[Subagent Context] parent session cc-1\nthe real question"),
    "the real question",
  )
})

test("a turn that is only a system reminder never reaches the extractor", () => {
  const decision = shouldCaptureText(
    "<system-reminder>the user opened a new file</system-reminder>",
    "user",
  )
  assert.equal(decision.shouldCapture, false)
  assert.equal(decision.reason, "empty")
})

test("findLastHumanTurnIndex skips tool results mapped onto the user role", () => {
  const turns = extractCaptureTurns(
    [
      { payload: { message: { role: "user", content: "compacted historical summary" } } },
      { payload: { message: { role: "user", content: "current user request" } } },
      { payload: { type: "function_call", id: "call-1", name: "shell", arguments: "{}" } },
      { payload: { type: "function_call_output", call_id: "call-1", output: "tool result" } },
      { payload: { message: { role: "assistant", content: "current assistant response" } } },
    ],
    CAPTURE_CONFIG,
  )

  const index = findLastHumanTurnIndex(turns)
  assert.equal(turns[index].text, "current user request")
  assert.equal(turns.at(-2).role, "user")
  assert.equal(turns.at(-2).parts[0].type, "tool")
})

test("findLastHumanTurnIndex reports -1 when no human turn survives", () => {
  const turns = extractCaptureTurns(
    [
      { payload: { type: "function_call", id: "call-1", name: "shell", arguments: "{}" } },
      { payload: { type: "function_call_output", call_id: "call-1", output: "tool result" } },
      { payload: { message: { role: "assistant", content: "assistant only" } } },
    ],
    CAPTURE_CONFIG,
  )

  assert.equal(findLastHumanTurnIndex(turns), -1)
  assert.equal(findLastHumanTurnIndex([]), -1)
})

/**
 * Claude Code and Codex each wrap this module in an adapter that reads their
 * own transcript shape and exports it under the same name. `export *` beside a
 * same-named local export lets the local one win with no diagnostic, so a
 * reader of the import cannot tell which function they hold; these two are the
 * adapters, and the shared names beside them are the shared ones. Each adapter
 * re-exports from its own vendored copy of this module, so the comparison is
 * against that copy rather than against lib/.
 */
test("the Claude Code adapter is the extractCaptureTurns its callers import", async () => {
  const cc = await import("../claude-code-memory-plugin/scripts/cc-transcript.mjs")
  const vendored = await import("../claude-code-memory-plugin/scripts/shared/capture-utils.mjs")

  assert.notEqual(cc.extractCaptureTurns, vendored.extractCaptureTurns)
  assert.equal(cc.sanitizeCapturedText, vendored.sanitizeCapturedText)

  // Claude nests a tool result in a content array; only the adapter flattens it.
  const anthropic = [{
    type: "user",
    message: {
      role: "user",
      content: [{ type: "tool_result", tool_use_id: "t1", content: [{ type: "text", text: "tool output" }] }],
    },
  }]
  assert.equal(cc.extractCaptureTurns(anthropic, CAPTURE_CONFIG)[0].parts[0].tool_output, "tool output")
  assert.equal(
    vendored.extractCaptureTurns(anthropic, CAPTURE_CONFIG)[0].parts[0].tool_output,
    JSON.stringify([{ type: "text", text: "tool output" }]),
  )
})

test("the Codex adapter is the extractCaptureTurns its callers import", async () => {
  const codex = await import("../codex-memory-plugin/scripts/capture-utils.mjs")
  const vendored = await import("../codex-memory-plugin/scripts/shared/capture-utils.mjs")

  assert.notEqual(codex.extractCaptureTurns, vendored.extractCaptureTurns)
  assert.equal(codex.findLastHumanTurnIndex, vendored.findLastHumanTurnIndex)

  // Codex's newer tool events carry no role at all until the adapter maps them.
  const rollout = [
    { type: "response_item", payload: { type: "custom_tool_call", call_id: "c1", name: "shell", input: "ls" } },
    { type: "response_item", payload: { type: "custom_tool_call_output", call_id: "c1", output: "file.txt" } },
  ]
  assert.equal(codex.extractCaptureTurns(rollout, CAPTURE_CONFIG).length, 2)
  assert.deepEqual(extractCaptureTurns(rollout, CAPTURE_CONFIG), [])
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

test("already-extracted parts are sanitized before capture rules run", () => {
  const parts = [
    { type: "text", text: "<system-reminder>approved</system-reminder>Remember SECRET123" },
    TOOL_PART,
  ]
  assert.equal(shapeCaptureParts(parts, "user", { captureFilters: ["k/approved/"] }).dropped, true)
  assert.deepEqual(shapeCaptureParts(parts, "user", {
    captureFilters: ["d/approved/", "s/SECRET123/[redacted]/g"],
  }).parts, [{ type: "text", text: "Remember [redacted]" }, TOOL_PART])
  assert.equal(parts[0].text.includes("SECRET123"), true)
})

test("shared capture filters sanitized conversation text before keep and drop", () => {
  const payload = { role: "user", content: "<openviking-context>approved</openviking-context>Remember private details." }
  const kept = shapeCapturePayload(payload, "user", { captureFilters: ["k/approved/"] })
  assert.equal(kept.dropped, true)
  assert.deepEqual(extractCaptureTurns([{ payload }], { captureFilters: ["k/approved/"] }), [])

  const dropped = shapeCapturePayload(payload, "user", { captureFilters: ["d/approved/"] })
  assert.equal(dropped.dropped, false)
  assert.deepEqual(dropped.parts, [{ type: "text", text: "Remember private details." }])
  assert.equal(dropped.text, "Remember private details.")
})

test("shared capture filters aggregate text and leave tool payloads intact", () => {
  const payload = { role: "assistant", content: [
    { type: "text", text: "Remember secret" },
    { type: "toolCall", id: "call-1", name: "lookup", arguments: { secret: true } },
  ] }
  assert.equal(shapeCapturePayload(payload, "assistant", {
    captureFilters: ["d/secret/"],
  }, { faithful: true }).dropped, true)

  const shaped = shapeCapturePayload(payload, "assistant", {
    captureFilters: ["s/secret/[redacted]/g"],
  }, { faithful: true })
  assert.equal(shaped.text, "Remember [redacted]")
  assert.equal(shaped.parts[1].tool_input.secret, true)

  const toolOnly = shapeCapturePayload({ role: "assistant", content: payload.content.slice(1) }, "assistant", {
    captureFilters: ["k/never matches/"],
  }, { faithful: true })
  assert.equal(toolOnly.dropped, false)
  assert.equal(toolOnly.parts[0].type, "tool")
})

test("shared capture uses conversation text for mixed messages regardless of rule presence", () => {
  const payload = { role: "assistant", content: [
    { type: "text", text: "Run this." },
    { type: "toolCall", id: "call-1", name: "lookup", arguments: { q: "x" } },
  ] }
  const baseline = shapeCapturePayload(payload, "assistant", {}, { faithful: true })
  assert.equal(baseline.text, "Run this.")
  assert.deepEqual(shapeCapturePayload(payload, "assistant", {
    captureFilters: ["user:d/.*/"],
  }, { faithful: true }), baseline)
  assert.deepEqual(shapeCapturePayload(payload, "assistant", {
    captureFilters: ["s/zzz/y/"],
  }, { faithful: true }), baseline)
  assert.equal(shapeCapturePayload(payload, "assistant", {
    captureFilters: ["assistant:s/Run/Do/"],
  }, { faithful: true }).text, "Do this.")
})

test("sanitation keeps ordinary timestamped log lines", () => {
  const log = "2024-01-01 12:00:00 ERROR something broke\n2024-01-01 12:00:01 INFO retry"
  assert.equal(sanitizeCapturedText(log), log)
  const turns = extractCaptureTurns([userEntry(log)], {})
  assert.equal(turns.length, 1)
  assert.deepEqual(turns[0].parts, [{ type: "text", text: log }])
  assert.equal(sanitizeCapturedText("[2024-01-01 12:00:00] Context"), "Context")
  assert.equal(sanitizeCapturedText("[2024-01-01 12:00:00 ERROR] failed"), "[2024-01-01 12:00:00 ERROR] failed")
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
