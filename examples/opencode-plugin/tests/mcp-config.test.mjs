import test from "node:test"
import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { fileURLToPath } from "node:url"
import { dirname, join, resolve } from "node:path"
import { createOpenVikingV2McpConfig } from "../lib/mcp-config.mjs"

const testDir = dirname(fileURLToPath(import.meta.url))

test("OpenCode v2 MCP config uses the local server schema", () => {
  const entry = createOpenVikingV2McpConfig("/tmp/ov")

  assert.equal(entry.type, "local")
  assert.equal(entry.command[0], "node")
  assert.match(entry.command[1], /servers[\\/]mcp-proxy\.mjs$/)
  assert.equal(entry.enabled, undefined)
  assert.equal(entry.codemode, false)
  assert.deepEqual(entry.timeout, { startup: 15000, catalog: 15000, execution: 15000 })
})

test("OpenCode plugin exports a v2 setup without importing @opencode/plugin", async () => {
  const source = await readFile(join(testDir, "../index.mjs"), "utf8")

  assert.match(source, /id:\s*"openviking"/)
  assert.match(source, /async setup\(ctx\)/)
  assert.match(source, /async server\(input\)/)
  assert.doesNotMatch(source, /@opencode\/plugin/)
})
