import test from "node:test"
import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"
import { createOpenVikingMcpConfig, injectOpenVikingMcpConfig } from "../lib/mcp-config.mjs"

const testDir = dirname(fileURLToPath(import.meta.url))

test("injectOpenVikingMcpConfig registers a local stdio MCP server", () => {
  const config = {}

  assert.equal(injectOpenVikingMcpConfig(config, "/tmp/openviking-plugin"), true)
  assert.deepEqual(config.mcp.openviking, {
    type: "local",
    command: ["node", "/tmp/openviking-plugin/servers/mcp-proxy.mjs"],
    enabled: true,
    timeout: 15000,
  })
})

test("injectOpenVikingMcpConfig respects explicit disabled MCP server", () => {
  const config = { mcp: { openviking: { enabled: false } } }

  assert.equal(injectOpenVikingMcpConfig(config, "/tmp/openviking-plugin"), false)
  assert.deepEqual(config.mcp.openviking, { enabled: false })
})

test("OpenCode plugin keeps tools on MCP rather than native tool hook", async () => {
  const source = await readFile(join(testDir, "../index.mjs"), "utf8")

  assert.match(source, /injectOpenVikingMcpConfig/)
  assert.doesNotMatch(source, /createMemoryTools/)
  assert.doesNotMatch(source, /createCodeTools/)
  assert.doesNotMatch(source, /\btool:\s*\{/)
})

test("OpenCode plugin uses real lifecycle hooks for capture flushing", async () => {
  const source = await readFile(join(testDir, "../index.mjs"), "utf8")

  assert.doesNotMatch(source, /"session\.idle":/)
  assert.doesNotMatch(source, /\bstop:\s*async/)
  assert.match(source, /\bdispose:\s*async/)
})

test("OpenCode MCP config points to the proxy entrypoint", () => {
  const entry = createOpenVikingMcpConfig("/tmp/ov")

  assert.equal(entry.type, "local")
  assert.equal(entry.command[0], "node")
  assert.match(entry.command[1], /servers\/mcp-proxy\.mjs$/)
})
