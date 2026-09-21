import test from "node:test"
import assert from "node:assert/strict"
import { guardVikingUriToolCall, noticeVikingUriToolResult } from "../lib/uri-guard-adapter.mjs"

test("pi URI guard blocks builtin file tools on viking URIs", () => {
  const decision = guardVikingUriToolCall({
    type: "tool_call",
    toolName: "read",
    input: { path: "viking://resources/project/file.md" },
  })

  assert.equal(decision?.block, true)
  assert.match(decision?.reason ?? "", /viking:\/\/ URIs are OpenViking virtual paths/)
  assert.match(decision?.reason ?? "", /Use viking_read instead/)
})

test("pi URI guard lets bash commands containing viking URI run", () => {
  const decision = guardVikingUriToolCall({
    type: "tool_call",
    toolName: "bash",
    input: { command: "cat viking://resources/project/file.md" },
  })

  assert.equal(decision, null)
})

test("pi URI guard allows normal local paths and OpenViking native tools", () => {
  assert.equal(guardVikingUriToolCall({ toolName: "read", input: { path: "/tmp/file.md" } }), null)
  assert.equal(guardVikingUriToolCall({ toolName: "viking_read", input: { uri: "viking://resources/file.md" } }), null)
  assert.equal(guardVikingUriToolCall({ toolName: "grep", input: { pattern: "viking://", path: "/repo" } }), null)
})

test("pi URI guard appends a notice to bash results whose command carried a viking URI", () => {
  const original = [{ type: "text", text: "cat: viking://resources/project/file.md: No such file or directory" }]
  const result = noticeVikingUriToolResult({
    type: "tool_result",
    toolName: "bash",
    toolCallId: "call-1",
    input: { command: "cat viking://resources/project/file.md" },
    content: original,
    isError: true,
  })

  assert.equal(result?.content.length, 2)
  assert.deepEqual(result.content[0], original[0])
  assert.equal(result.content[1].type, "text")
  assert.match(result.content[1].text, /viking:\/\/resources\/project\/file\.md/)
  assert.match(result.content[1].text, /use viking_read or viking_search instead/)
  assert.match(result.content[1].text, /viking_read\(uri="viking:\/\/resources\/project\/file\.md", level="overview"\)/)
  assert.match(result.content[1].text, /ignore this notice\.$/)
  assert.equal(original.length, 1)
})

test("pi URI guard leaves other tool results alone", () => {
  const content = [{ type: "text", text: "ok" }]
  assert.equal(noticeVikingUriToolResult({ toolName: "bash", input: { command: "ls /tmp" }, content }), null)
  assert.equal(noticeVikingUriToolResult({ toolName: "read", input: { path: "viking://resources/file.md" }, content }), null)
  assert.equal(noticeVikingUriToolResult({ toolName: "viking_read", input: { uri: "viking://resources/file.md" }, content }), null)
})
