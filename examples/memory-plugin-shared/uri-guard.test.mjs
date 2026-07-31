import test from "node:test"
import assert from "node:assert/strict"
import { buildGuardMessage, findVikingUri, findVikingUriInValue, normalizeToolName } from "./lib/uri-guard.mjs"

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

test("normalizeToolName trims and lowercases", () => {
  assert.equal(normalizeToolName(" Read "), "read")
})
