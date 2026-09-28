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
  assert.match(decision?.reason ?? "", /Use openviking_read instead/)
  assert.match(decision?.reason ?? "", /openviking_read\(uris=\["viking:\/\/resources\/project\/file\.md"\]\)/)
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
  assert.equal(guardVikingUriToolCall({ toolName: "openviking_read", input: { uris: ["viking://resources/file.md"] } }), null)
  assert.equal(guardVikingUriToolCall({ toolName: "grep", input: { pattern: "viking://", path: "/repo" } }), null)
})

test("pi URI guard blocks an edit on a viking path but not one that merely mentions a URI", () => {
  const decision = guardVikingUriToolCall({
    type: "tool_call",
    toolName: "edit",
    input: {
      path: "viking://memories/project/notes.md",
      edits: [{ oldText: "old", newText: "new" }],
    },
  })

  assert.equal(decision?.block, true)
  assert.match(decision?.reason ?? "", /Use openviking_edit instead/)
  assert.match(
    decision?.reason ?? "",
    /openviking_edit\(uri="viking:\/\/memories\/project\/notes\.md", old_string="\.\.\.", new_string="\.\.\."\)/,
  )

  // Editing a local file whose replacement text carries a viking:// URI — what
  // happens whenever somebody edits this repo's own docs. pi's edits[] keys are
  // not in the shared guard's content-key allowlist, so the adapter narrows the
  // input to { path } before the guard sees it.
  assert.equal(
    guardVikingUriToolCall({
      toolName: "edit",
      input: {
        path: "/repo/docs/agent-integrations/11-pi.md",
        edits: [{ oldText: "see the docs", newText: "read viking://memories/project/notes.md instead" }],
      },
    }),
    null,
  )
  // Same edit in pi's legacy top-level spelling.
  assert.equal(
    guardVikingUriToolCall({
      toolName: "edit",
      input: { path: "/repo/docs/README.md", oldText: "see the docs", newText: "viking://memories/project/notes.md" },
    }),
    null,
  )
})

test("pi URI guard blocks a write to a viking path but not a local write that mentions one", () => {
  assert.equal(
    guardVikingUriToolCall({ toolName: "write", input: { path: "viking://memories/a.md", content: "x" } })?.block,
    true,
  )
  assert.equal(
    guardVikingUriToolCall({ toolName: "write", input: { path: "/tmp/a.md", content: "viking://memories/a.md" } }),
    null,
  )
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
  assert.match(result.content[1].text, /use openviking_read or openviking_search instead/)
  assert.match(result.content[1].text, /openviking_read\(uris=\["viking:\/\/resources\/project\/file\.md"\]\)/)
  assert.match(result.content[1].text, /ignore this notice\.$/)
  assert.equal(original.length, 1)
})

test("pi URI guard leaves other tool results alone", () => {
  const content = [{ type: "text", text: "ok" }]
  assert.equal(noticeVikingUriToolResult({ toolName: "bash", input: { command: "ls /tmp" }, content }), null)
  assert.equal(noticeVikingUriToolResult({ toolName: "read", input: { path: "viking://resources/file.md" }, content }), null)
  assert.equal(noticeVikingUriToolResult({ toolName: "openviking_read", input: { uris: ["viking://resources/file.md"] }, content }), null)
})
