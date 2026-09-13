import test from "node:test"
import assert from "node:assert/strict"
import { guardVikingUriToolCall } from "../lib/uri-guard-adapter.mjs"

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

test("pi URI guard blocks bash commands containing viking URI", () => {
  const decision = guardVikingUriToolCall({
    type: "tool_call",
    toolName: "bash",
    input: { command: "cat viking://resources/project/file.md" },
  })

  assert.equal(decision?.block, true)
  assert.match(decision?.reason ?? "", /Use viking_read or viking_search instead/)
})

test("pi URI guard allows normal local paths and OpenViking native tools", () => {
  assert.equal(guardVikingUriToolCall({ toolName: "read", input: { path: "/tmp/file.md" } }), null)
  assert.equal(guardVikingUriToolCall({ toolName: "viking_read", input: { uri: "viking://resources/file.md" } }), null)
})
