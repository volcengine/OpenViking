import test from "node:test"
import assert from "node:assert/strict"
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { loadConfig } from "../lib/config.mjs"
import plugin, { OpenVikingPlugin } from "../index.mjs"

// The layers, the knobs and the peer are the shared loader's, and
// memory-plugin-shared/plugin-config.test.mjs holds this harness to them. What
// is left here is hook-only mode, which is this plugin's alone: it is the only
// harness that registers an MCP server from inside the host's own config.

async function withTempDir(prefix, fn) {
  const dir = await mkdtemp(join(tmpdir(), prefix))
  try {
    return await fn(dir)
  } finally {
    await rm(dir, { recursive: true, force: true })
  }
}

function restoreOpenVikingEnv(snapshot) {
  for (const key of Object.keys(process.env)) {
    if (key.startsWith("OPENVIKING_")) delete process.env[key]
  }
  for (const [key, value] of Object.entries(snapshot)) {
    if (key.startsWith("OPENVIKING_")) process.env[key] = value
  }
}

test("loadConfig supports hook-only mode without registering the bundled MCP server", async () => {
  const snapshot = { ...process.env }
  await withTempDir("ov-oc-hook-only-", async (dir) => {
    try {
      for (const key of Object.keys(process.env)) {
        if (key.startsWith("OPENVIKING_")) delete process.env[key]
      }
      const project = join(dir, "project")
      const ovcli = join(dir, "ovcli.conf")
      await writeFile(ovcli, JSON.stringify({
        url: "https://cli.example.com",
        plugin: { opencode: { mcpEnabled: false } },
      }))
      process.env.OPENVIKING_CLI_CONFIG_FILE = ovcli

      const cfg = loadConfig(dir, project)
      assert.equal(cfg.enabled, true)
      assert.equal(cfg.mcp.enabled, false)
    } finally {
      restoreOpenVikingEnv(snapshot)
    }
  })
})

test("OpenCode 1.x gets an empty hooks object", async () => {
  const snapshot = { ...process.env }
  await withTempDir("ov-oc-v1-", async (dir) => {
    try {
      for (const key of Object.keys(process.env)) {
        if (key.startsWith("OPENVIKING_")) delete process.env[key]
      }
      const ovcli = join(dir, "ovcli.conf")
      await writeFile(ovcli, JSON.stringify({ url: "http://127.0.0.1:9", plugin: { dataDir: join(dir, "data") } }))
      process.env.OPENVIKING_CLI_CONFIG_FILE = ovcli
      process.env.OPENVIKING_CONFIG_FILE = join(dir, "ov.conf")
      process.env.OPENVIKING_HOME = dir
      assert.deepEqual(await OpenVikingPlugin({ directory: dir }), {})
      assert.deepEqual(await plugin.server({ directory: dir }), {})
      assert.match(await readFile(join(dir, "data", "openviking-memory.log"), "utf8"), /OpenCode 1\.x is no longer supported/)
    } finally {
      restoreOpenVikingEnv(snapshot)
    }
  })
})

test("loadConfig preserves an explicit zero commit keep recent count", async () => {
  const snapshot = { ...process.env }
  await withTempDir("ov-oc-keep-recent-zero-", async (dir) => {
    try {
      for (const key of Object.keys(process.env)) {
        if (key.startsWith("OPENVIKING_")) delete process.env[key]
      }
      const project = join(dir, "project")
      process.env.OPENVIKING_CREDENTIAL_SOURCE = "env"
      process.env.OPENVIKING_URL = "https://env.example.com"
      const ovcli = join(dir, "ovcli.conf")
      await writeFile(ovcli, JSON.stringify({
        url: "https://env.example.com",
        plugin: { commitKeepRecentCount: 0 },
      }))
      process.env.OPENVIKING_CLI_CONFIG_FILE = ovcli

      const cfg = loadConfig(dir, project)
      assert.equal(cfg.commitKeepRecentCount, 0)
    } finally {
      restoreOpenVikingEnv(snapshot)
    }
  })
})

test("loadConfig defaults an invalid commit keep recent count", async () => {
  const snapshot = { ...process.env }
  await withTempDir("ov-oc-keep-recent-invalid-", async (dir) => {
    try {
      for (const key of Object.keys(process.env)) {
        if (key.startsWith("OPENVIKING_")) delete process.env[key]
      }
      const project = join(dir, "project")
      process.env.OPENVIKING_CREDENTIAL_SOURCE = "env"
      process.env.OPENVIKING_URL = "https://env.example.com"
      const ovcli = join(dir, "ovcli.conf")
      await writeFile(ovcli, JSON.stringify({
        url: "https://env.example.com",
        plugin: { commitKeepRecentCount: null },
      }))
      process.env.OPENVIKING_CLI_CONFIG_FILE = ovcli

      const cfg = loadConfig(dir, project)
      assert.equal(cfg.commitKeepRecentCount, 10)
    } finally {
      restoreOpenVikingEnv(snapshot)
    }
  })
})
