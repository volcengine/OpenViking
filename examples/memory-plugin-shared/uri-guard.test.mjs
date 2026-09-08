import test from "node:test"
import assert from "node:assert/strict"
import {
  buildGuardMessage,
  denyCursorPermission,
  denyHookSpecificOutput,
  evaluateUriGuard,
  findVikingUri,
  findVikingUriInValue,
  normalizeToolName,
} from "./lib/uri-guard.mjs"

test("findVikingUri detects common path and URI argument keys", () => {
  assert.equal(findVikingUri({ filePath: "viking://resources/a.md" }), "viking://resources/a.md")
  assert.equal(findVikingUri({ file_path: "viking://resources/b.md" }), "viking://resources/b.md")
  assert.equal(findVikingUri({ target_uri: "viking://resources/c/" }), "viking://resources/c/")
  assert.equal(findVikingUri({ path: "/tmp/file.md" }), null)
})

test("findVikingUri detects nested command strings", () => {
  assert.equal(
    findVikingUri({ args: { command: "cat viking://resources/project/file.md" } }),
    "viking://resources/project/file.md",
  )
  assert.equal(
    findVikingUriInValue(["grep", "needle", "viking://resources/project/"]),
    "viking://resources/project/",
  )
})

test("buildGuardMessage names replacement tool and example", () => {
  const message = buildGuardMessage("viking://resources/project/file.md", {
    tool: "openviking_read",
    example: 'openviking_read(uri="viking://resources/project/file.md")',
  })

  assert.match(message, /virtual paths/)
  assert.match(message, /Use openviking_read instead/)
  assert.match(message, /Example:/)
})

test("evaluateUriGuard denies a guarded tool and passes everything else", () => {
  const denied = evaluateUriGuard("Read", { file_path: "viking://resources/a.md" })
  assert.equal(denied?.uri, "viking://resources/a.md")
  assert.match(denied?.reason ?? "", /Use OpenViking MCP read instead/)
  assert.match(denied?.reason ?? "", /Example: read\(uris="viking:\/\/resources\/a\.md"\)/)

  assert.equal(evaluateUriGuard("Read", { file_path: "/tmp/a.md" }), null)
  assert.equal(evaluateUriGuard("Task", { file_path: "viking://resources/a.md" }), null)
})

// The guarded set is per host: claude-code and opencode never see the shell in
// their matchers, pi does. Each of them pins its own half in its own tests.
test("evaluateUriGuard only guards the tools the host names", () => {
  const call = ["bash", { command: "cat viking://resources/a.md" }]
  assert.equal(evaluateUriGuard(...call, { guarded: new Set(["read", "glob", "grep"]) }), null)
  assert.equal(
    evaluateUriGuard(...call, { guarded: new Set(["read", "bash"]) })?.uri,
    "viking://resources/a.md",
  )
})

test("evaluateUriGuard takes the host's own replacement tools and examples", () => {
  const hints = {
    read: { tool: "viking_read", example: (uri) => `viking_read(uri="${uri}", level="overview")` },
  }
  const decision = evaluateUriGuard("read", { path: "viking://resources/a.md" }, { hints })
  assert.match(decision?.reason ?? "", /Use viking_read instead/)
  assert.match(decision?.reason ?? "", /level="overview"/)
  assert.equal(evaluateUriGuard("glob", { pattern: "viking://resources/**" }, { hints }), null)
})

test("deny envelopes carry only the keys their host recognizes", () => {
  assert.deepEqual(denyHookSpecificOutput("because"), {
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: "because",
    },
  })
  assert.deepEqual(denyCursorPermission("because"), { permission: "deny", user_message: "because" })
  assert.deepEqual(denyCursorPermission("because", { agentMessage: true }), {
    permission: "deny",
    user_message: "because",
    agent_message: "because",
  })
})

test("normalizeToolName trims and lowercases", () => {
  assert.equal(normalizeToolName(" Read "), "read")
})
