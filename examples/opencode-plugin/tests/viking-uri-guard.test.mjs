import test from "node:test"
import assert from "node:assert/strict"
import { createVikingUriGuard, findVikingUri } from "../lib/viking-uri-guard.mjs"

test("viking uri guard blocks filesystem tools on virtual URIs", async () => {
  const guard = createVikingUriGuard()

  assert.equal(findVikingUri({ filePath: "viking://resources/project/file.md" }), "viking://resources/project/file.md")
  assert.equal(findVikingUri({ path: "/tmp/file.md" }), null)

  await assert.rejects(
    () => guard({ tool: "read" }, { args: { filePath: "viking://resources/project/file.md" } }),
    /Use memread instead/,
  )
  await assert.rejects(
    () => guard({ tool: "glob" }, { args: { path: "viking://resources/project/" } }),
    /Use membrowse instead/,
  )
  await assert.rejects(
    () => guard({ tool: "grep" }, { args: { path: "viking://resources/project/", pattern: "SessionManager" } }),
    /Use memsearch instead/,
  )

  await assert.doesNotReject(() => guard({ tool: "read" }, { args: { filePath: "/tmp/file.md" } }))
  await assert.doesNotReject(() => guard({ tool: "bash" }, { args: { command: "cat viking://resources/project/file.md" } }))
})
