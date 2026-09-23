import test from "node:test"
import assert from "node:assert/strict"
import {
  buildGuardMessage,
  buildGuardNotice,
  denyCursorPermission,
  denyHookSpecificOutput,
  evaluateUriGuard,
  evaluateUriNotice,
  findVikingUri,
  findVikingUriInValue,
  isSkillUri,
  noticeHookSpecificOutput,
  normalizeToolName,
  preToolUseOutput,
  readToolEvent,
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
  assert.equal(evaluateUriGuard("Write", { file_path: "viking://resources/a.md", content: "x" })?.uri, "viking://resources/a.md")
  assert.equal(evaluateUriGuard("Write", { file_path: "/tmp/a.md", content: "see viking://resources/a.md" }), null)
})

test("a shell command that carries a viking:// URI gets a notice, never a deny", () => {
  for (const [tool, input] of [
    ["Bash", { command: "ov read viking://resources/a.md" }],
    ["RunCommand", { command: "cat viking://resources/a.md" }],
    ["shell", { command: ["curl", "-d", "{\"uri\":\"viking://resources/a.md\"}", "http://127.0.0.1:1933"] }],
  ]) {
    assert.equal(evaluateUriGuard(tool, input), null, tool)
    const notice = evaluateUriNotice(tool, input)
    assert.equal(notice?.uri, "viking://resources/a.md", tool)
    assert.match(notice?.reason ?? "", /ignore this notice/, tool)
  }
  assert.equal(evaluateUriNotice("Bash", { command: "cat /tmp/a.md" }), null)
})

test("a file tool on a viking:// path is denied, never noticed", () => {
  const input = { file_path: "viking://resources/a.md" }
  assert.equal(evaluateUriGuard("Read", input)?.uri, "viking://resources/a.md")
  assert.equal(evaluateUriNotice("Read", input), null)
  assert.equal(evaluateUriNotice("Task", { command: "viking://resources/a.md" }), null)
})

test("grep's pattern is search text while glob's pattern is a location", () => {
  assert.equal(evaluateUriGuard("Grep", { pattern: "viking://", path: "/repo" }), null)
  assert.equal(
    evaluateUriGuard("Grep", { pattern: "viking://", path: "viking://resources/project/" })?.uri,
    "viking://resources/project/",
  )
  assert.equal(evaluateUriGuard("Glob", { pattern: "viking://resources/**" })?.uri, "viking://resources/**")
})

test("buildGuardNotice names the plugin, the URI, the replacement and the way out", () => {
  const notice = buildGuardNotice("viking://resources/a.md", {
    tool: "openviking_read",
    example: 'openviking_read(uris=["viking://resources/a.md"])',
  })
  assert.match(notice, /OpenViking memory plugin/)
  assert.match(notice, /viking:\/\/resources\/a\.md/)
  assert.match(notice, /use openviking_read instead/)
  assert.match(notice, /Example: openviking_read/)
  assert.match(notice, /ignore this notice/)
  assert.doesNotMatch(buildGuardNotice("viking://resources/a.md"), /Example:/)
})

test("evaluateUriGuard takes the host's own replacement tools and examples", () => {
  // The shape pi's adapter passes in.
  const hints = {
    read: { tool: "openviking_read", example: (uri) => `openviking_read(uris=["${uri}"])` },
  }
  const decision = evaluateUriGuard("read", { path: "viking://resources/a.md" }, { hints })
  assert.match(decision?.reason ?? "", /Use openviking_read instead/)
  assert.match(decision?.reason ?? "", /uris=\["viking:\/\/resources\/a\.md"\]/)
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
})

test("the notice envelope leaves the permission decision to the host", () => {
  assert.deepEqual(noticeHookSpecificOutput("because"), {
    hookSpecificOutput: { hookEventName: "PreToolUse", additionalContext: "because" },
  })
})

test("preToolUseOutput denies file paths, notices shell commands, and passes the rest", () => {
  const denied = preToolUseOutput({ tool_name: "Read", tool_input: { file_path: "viking://resources/a.md" } })
  assert.equal(denied.hookSpecificOutput?.permissionDecision, "deny")

  const noticed = preToolUseOutput({ tool_name: "Bash", tool_input: { command: "ov read viking://resources/a.md" } })
  assert.equal(noticed.hookSpecificOutput?.permissionDecision, undefined)
  assert.match(noticed.hookSpecificOutput?.additionalContext ?? "", /ignore this notice/)

  assert.deepEqual(preToolUseOutput({ tool_name: "Bash", tool_input: { command: "ls" } }), {})
  assert.deepEqual(preToolUseOutput({ tool_name: "Task", tool_input: { prompt: "viking://resources/a.md" } }), {})
  assert.deepEqual(preToolUseOutput(), {})
})

test("readToolEvent accepts each host's spelling", () => {
  const input = { file_path: "/tmp/a.md" }
  for (const event of [
    { tool_name: "Read", tool_input: input },
    { toolName: "Read", toolInput: input },
    { name: "Read", input },
    { tool: "Read", input },
  ]) {
    assert.deepEqual(readToolEvent(event), { toolName: "Read", toolInput: input })
  }
  assert.deepEqual(readToolEvent({}), { toolName: undefined, toolInput: {} })
})

test("normalizeToolName trims and lowercases", () => {
  assert.equal(normalizeToolName(" Read "), "read")
})

test("a write or edit aimed at a skill points at add_skill, not the refused write tool", () => {
  const cases = [
    ["viking://~/skills/pr-review/SKILL.md", 'add_skill(data="<the full SKILL.md text>")'],
    ["viking://user/alice/skills/pr-review", 'add_skill(data="<the full SKILL.md text>")'],
    // Back to the shared root: without target_uri add_skill makes a private copy.
    ["viking://agent/skills/pr-review/SKILL.md", 'add_skill(data="<the full SKILL.md text>", target_uri="viking://agent/skills")'],
    // A helper file changes through a folder upload, not SKILL.md text.
    ["viking://agent/skills/pr-review/scripts/run.sh", 'add_skill(path="<local skill folder or .zip with the changed files>", target_uri="viking://agent/skills")'],
  ]
  for (const [uri, example] of cases) {
    assert.equal(isSkillUri(uri), true, uri)
    const { reason } = evaluateUriGuard("Write", { file_path: uri })
    assert.match(reason, /Use OpenViking MCP add_skill instead\./, uri)
    assert.ok(reason.includes(`Example: ${example}`), `${uri}: ${reason}`)
  }
  const { reason: edit } = evaluateUriGuard("Edit", { file_path: "viking://agent/skills/pr-review/SKILL.md" })
  assert.ok(edit.includes('add_skill(data="<the full edited SKILL.md text>", target_uri="viking://agent/skills")'), edit)

  assert.equal(isSkillUri("viking://resources/skills/notes.md"), false)
  const { reason } = evaluateUriGuard("Write", { file_path: "viking://~/notes/todo.md" })
  assert.match(reason, /Use OpenViking MCP write instead\./)
})
