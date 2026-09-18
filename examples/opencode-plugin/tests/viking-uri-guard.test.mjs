import test from "node:test"
import assert from "node:assert/strict"
import { createVikingUriGuard, createVikingUriNotice, findVikingUri } from "../lib/viking-uri-guard.mjs"

test("viking uri guard blocks filesystem tools on virtual URIs", async () => {
  const guard = createVikingUriGuard()

  assert.equal(findVikingUri({ filePath: "viking://resources/project/file.md" }), "viking://resources/project/file.md")
  assert.equal(findVikingUri({ path: "/tmp/file.md" }), null)

  await assert.rejects(
    () => guard({ tool: "read" }, { args: { filePath: "viking://resources/project/file.md" } }),
    /openviking_read\(uris=\["viking:\/\/resources\/project\/file\.md"\]\)/,
  )
  await assert.rejects(
    () => guard({ tool: "glob" }, { args: { path: "viking://resources/project/" } }),
    /Use openviking_glob instead/,
  )
  await assert.rejects(
    () => guard({ tool: "grep" }, { args: { path: "viking://resources/project/", pattern: "SessionManager" } }),
    /Use openviking_search instead/,
  )

  await assert.doesNotReject(() => guard({ tool: "read" }, { args: { filePath: "/tmp/file.md" } }))
  await assert.doesNotReject(() => guard({ tool: "bash" }, { args: { command: "cat viking://resources/project/file.md" } }))
  await assert.doesNotReject(() => guard({ tool: "grep" }, { args: { pattern: "viking://", path: "/repo" } }))
})

test("viking uri notice appends to shell output and keeps the original text", async () => {
  const notice = createVikingUriNotice()
  const output = { title: "cat", output: "cat: viking://resources/project/file.md: No such file or directory", metadata: {} }

  await notice({ tool: "bash", args: { command: "cat viking://resources/project/file.md" } }, output)

  assert.ok(output.output.startsWith("cat: viking://resources/project/file.md: No such file or directory\n\n"))
  assert.match(output.output, /viking:\/\/resources\/project\/file\.md/)
  assert.match(output.output, /openviking_read\(uris=\["viking:\/\/resources\/project\/file\.md"\]\)/)
  assert.match(output.output, /ignore this notice/)
  assert.equal(output.title, "cat")
})

test("viking uri notice fills empty shell output", async () => {
  const notice = createVikingUriNotice()
  const output = { title: "ov", output: "", metadata: {} }

  await notice({ tool: "bash", args: { command: "ov read viking://resources/project/file.md" } }, output)

  assert.match(output.output, /^\[OpenViking memory plugin\] URI guard/)
  assert.match(output.output, /ignore this notice/)
})

test("viking uri notice leaves file tools and plain shell commands alone", async () => {
  const notice = createVikingUriNotice()
  const cases = [
    { tool: "read", args: { filePath: "viking://resources/project/file.md" } },
    { tool: "grep", args: { pattern: "viking://", path: "/repo" } },
    { tool: "bash", args: { command: "ls /tmp" } },
    { tool: "openviking_read", args: { uris: ["viking://resources/project/file.md"] } },
  ]

  for (const input of cases) {
    const output = { title: "t", output: "original", metadata: {} }
    await notice(input, output)
    assert.equal(output.output, "original", input.tool)
  }
})
