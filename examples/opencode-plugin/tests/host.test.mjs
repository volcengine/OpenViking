import test from "node:test"
import assert from "node:assert/strict"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import host, { createOpencodeHost, decode } from "../host.mjs"
import { turnMessages } from "../lib/shared/flow/capture.mjs"
import { toolCall } from "../lib/shared/flow/index.mjs"
import { buildConfigForTest } from "../../memory-plugin-shared/testing/support.mjs"

const cfg = buildConfigForTest("opencode")

const conversation = [
  { id: "msg_user", type: "user", text: "run the build" },
  { id: "msg_assistant", type: "assistant", content: [
    { type: "reasoning", text: "thinking" },
    { type: "text", text: "building" },
    { type: "tool", id: "call_1", name: "shell", state: { status: "completed", input: { command: "make" }, content: [{ type: "text", text: "built" }] } },
  ] },
  { id: "msg_pending", type: "assistant", content: [] },
]

test("decode sends user text and assistant text and tool parts, once per message id", () => {
  const decoded = decode({ cursor: {}, payload: { messages: conversation } })
  assert.deepEqual(turnMessages(decoded, cfg), [
    { role: "user", parts: [{ type: "text", text: "run the build" }], turn_id: "msg_user" },
    {
      role: "assistant",
      parts: [
        { type: "text", text: "building" },
        { type: "tool", tool_id: "call_1", tool_name: "shell", tool_status: "completed", tool_input: { command: "make" }, tool_output: "built" },
      ],
      turn_id: "msg_assistant",
    },
  ])
  const cursor = decoded.cursorAfter(2)
  assert.deepEqual(cursor, { ids: ["msg_user", "msg_assistant"] })
  // The assistant message still streaming is sent once it has content.
  const grown = [...conversation.slice(0, 2), { id: "msg_pending", type: "assistant", content: [{ type: "text", text: "done" }] }]
  assert.deepEqual(decode({ cursor, payload: { messages: grown } }).turns.map((turn) => turn.turnId), ["msg_pending"])
  assert.deepEqual(decode({ cursor: decoded.cursorAfter(1), payload: { messages: conversation } }).turns.map((turn) => turn.turnId), ["msg_assistant"])
})

test("an upgraded session resumes from the 0.5 storage cursor and state file", async () => {
  assert.deepEqual(decode({ cursor: { after: "msg_user" }, payload: { messages: conversation } }).turns.map((turn) => turn.turnId), ["msg_assistant"])
  const after = decode({ cursor: { after: "msg_assistant" }, payload: { messages: conversation } })
  assert.deepEqual(after.turns, [])
  assert.deepEqual(after.cursorAfter(0), { ids: ["msg_user", "msg_assistant"] })

  const dataDir = await mkdtemp(join(tmpdir(), "ov-oc-legacy-"))
  try {
    await writeFile(join(dataDir, "openviking-session-state.json"), JSON.stringify({
      version: 2,
      sessions: { ses_1: { ovSessionId: "oc-ses_1", messages: [["msg_user", { captured: true }], ["msg_assistant", { captured: false }]] } },
    }))
    const legacyHost = createOpencodeHost({ dataDir })
    const read = legacyHost.read("execution.ended", { sessionID: "ses_1", storedCursor: undefined })
    assert.deepEqual(legacyHost.legacyCursor("ses_1", read), { ids: ["msg_user"] })
    assert.equal(legacyHost.legacyCursor("ses_2", legacyHost.read("execution.ended", { sessionID: "ses_2" })), undefined)
  } finally {
    await rm(dataDir, { recursive: true, force: true })
  }
})

test("read keeps the session id and captures a subagent under its parent", () => {
  assert.deepEqual(
    host.read("prompt", { sessionID: "ses_1", directory: "/repo", prompt: " hi ", messageID: "msg_1" }),
    { sessionId: "ses_1", cwd: "/repo", prompt: "hi", eventId: "msg_1", legacy: { sessionID: "ses_1", after: undefined } },
  )
  const child = host.read("execution.ended", { sessionID: "ses_child", parentID: "ses_1" })
  assert.equal(child.sessionId, "ses_1")
  assert.equal(child.child, "subagent-ses_child")
})

test("tool calls: viking:// guard and session_history fill", () => {
  assert.match(toolCall(host, { sessionID: "ses_1", tool: "read", input: { filePath: "viking://resources/a" } }).deny, /openviking_read/)
  assert.match(toolCall(host, { sessionID: "ses_1", tool: "bash", input: { command: "cat viking://resources/a" } }).notice ?? "", /openviking_read/)
  assert.deepEqual(toolCall(host, { sessionID: "ses_1", tool: "read", input: { filePath: "/tmp/a" } }), {})
  assert.deepEqual(
    toolCall(host, { sessionID: "ses_1", tool: "openviking_session_history", input: { query: "x" } }),
    { input: { query: "x", session: "oc-ses_1" } },
  )
  assert.deepEqual(
    toolCall(host, { sessionID: "ses_c", parentID: "ses_1", tool: "openviking_session_history", input: {} }),
    { input: { session: "oc-ses_1__subagent-ses_c" } },
  )
  assert.deepEqual(toolCall(host, { sessionID: "ses_1", tool: "openviking_session_history", input: { session: "oc-other" } }), {})
})
