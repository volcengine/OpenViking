import test from "node:test"
import assert from "node:assert/strict"
import { createServer } from "node:http"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { loadConfig } from "../lib/config.mjs"
import { OpenVikingPlugin } from "../index.mjs"

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

async function withHealthServer(fn) {
  const server = createServer((request, response) => {
    response.setHeader("Content-Type", "application/json")
    if (request.url === "/health") {
      response.end(JSON.stringify({ status: "ok" }))
      return
    }
    response.statusCode = 404
    response.end(JSON.stringify({ status: "error" }))
  })
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve))
  try {
    const { port } = server.address()
    return await fn(`http://127.0.0.1:${port}`)
  } finally {
    await new Promise((resolve) => server.close(resolve))
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

test("OpenVikingPlugin keeps lifecycle hooks without mutating MCP config in hook-only mode", async () => {
  const snapshot = { ...process.env }
  await withTempDir("ov-oc-hook-only-runtime-", async (dir) => {
    await withHealthServer(async (endpoint) => {
      try {
        for (const key of Object.keys(process.env)) {
          if (key.startsWith("OPENVIKING_")) delete process.env[key]
        }
        const ovcli = join(dir, "ovcli.conf")
        await writeFile(ovcli, JSON.stringify({
          url: endpoint,
          plugin: {
            opencode: {
              mcpEnabled: false,
              dataDir: join(dir, "runtime"),
              repoContext: false,
              autoRecall: false,
              autoCapture: false,
            },
          },
        }))
        process.env.OPENVIKING_CLI_CONFIG_FILE = ovcli
        process.env.OPENVIKING_URL = endpoint

        const plugin = await OpenVikingPlugin({ client: {}, directory: dir })
        assert.equal(typeof plugin.event, "function")
        assert.equal(typeof plugin["chat.message"], "function")
        assert.equal(typeof plugin.dispose, "function")

        const opencodeConfig = { mcp: { external: { type: "remote", url: "https://example.com/mcp" } } }
        await plugin.config(opencodeConfig)
        assert.deepEqual(opencodeConfig, {
          mcp: { external: { type: "remote", url: "https://example.com/mcp" } },
        })
        await plugin.dispose()
      } finally {
        restoreOpenVikingEnv(snapshot)
      }
    })
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
