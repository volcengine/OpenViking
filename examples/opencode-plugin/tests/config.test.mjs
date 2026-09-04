import test from "node:test"
import assert from "node:assert/strict"
import { createServer } from "node:http"
import { mkdtemp, rm, writeFile, mkdir } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { loadConfig } from "../lib/config.mjs"
import { OpenVikingPlugin } from "../index.mjs"

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

test("loadConfig prefers env credentials over ovcli and legacy config", async () => {
  const snapshot = { ...process.env }
  await withTempDir("ov-oc-config-", async (dir) => {
    try {
      for (const key of Object.keys(process.env)) {
        if (key.startsWith("OPENVIKING_")) delete process.env[key]
      }
      const ovcli = join(dir, "ovcli.conf")
      const project = join(dir, "project")
      await mkdir(join(project, ".opencode"), { recursive: true })
      await writeFile(ovcli, JSON.stringify({
        url: "https://cli.example.com",
        api_key: "cli-key",
        account: "cli-account",
        user: "cli-user",
        actor_peer_id: "cli-peer",
      }))
      await writeFile(join(project, ".opencode", "openviking-config.json"), JSON.stringify({
        endpoint: "https://legacy.example.com",
        apiKey: "legacy-key",
        account: "legacy-account",
        user: "legacy-user",
        peerId: "legacy-peer",
      }))
      process.env.OPENVIKING_CLI_CONFIG_FILE = ovcli
      process.env.OPENVIKING_URL = "https://env.example.com"
      process.env.OPENVIKING_API_KEY = "env-key"
      process.env.OPENVIKING_ACCOUNT = "env-account"
      process.env.OPENVIKING_USER = "env-user"
      process.env.OPENVIKING_PEER_ID = "env-peer"

      const cfg = loadConfig(dir, project)
      assert.equal(cfg.endpoint, "https://env.example.com")
      assert.equal(cfg.apiKey, "env-key")
      assert.equal(cfg.account, "env-account")
      assert.equal(cfg.user, "env-user")
      assert.equal(cfg.peerId, "env-peer")
      assert.equal(cfg.effectivePeer.peerId, "env-peer")
      assert.equal(cfg.effectivePeer.source, "explicit")
      assert.equal(cfg.legacyCredentialsUsed, false)
    } finally {
      restoreOpenVikingEnv(snapshot)
    }
  })
})

test("loadConfig reads legacy credentials as fallback and marks deprecation", async () => {
  const snapshot = { ...process.env }
  await withTempDir("ov-oc-legacy-", async (dir) => {
    try {
      for (const key of Object.keys(process.env)) {
        if (key.startsWith("OPENVIKING_")) delete process.env[key]
      }
      const project = join(dir, "project")
      process.env.OPENVIKING_CLI_CONFIG_FILE = join(dir, "missing-ovcli.conf")
      process.env.OPENVIKING_CONFIG_FILE = join(dir, "missing-ov.conf")
      await mkdir(join(project, ".opencode"), { recursive: true })
      await writeFile(join(project, ".opencode", "openviking-config.json"), JSON.stringify({
        endpoint: "https://legacy.example.com",
        apiKey: "legacy-key",
        account: "legacy-account",
        user: "legacy-user",
        peerId: "legacy-peer",
      }))

      const cfg = loadConfig(dir, project)
      assert.equal(cfg.endpoint, "https://legacy.example.com")
      assert.equal(cfg.apiKey, "legacy-key")
      assert.equal(cfg.account, "legacy-account")
      assert.equal(cfg.user, "legacy-user")
      assert.equal(cfg.peerId, "legacy-peer")
      assert.equal(cfg.effectivePeer.peerId, "legacy-peer")
      assert.equal(cfg.effectivePeer.source, "explicit")
      assert.equal(cfg.legacyCredentialsUsed, true)
    } finally {
      restoreOpenVikingEnv(snapshot)
    }
  })
})

test("loadConfig derives workspace peer by default", async () => {
  const snapshot = { ...process.env }
  await withTempDir("ov-oc-ws-", async (dir) => {
    try {
      for (const key of Object.keys(process.env)) {
        if (key.startsWith("OPENVIKING_")) delete process.env[key]
      }
      process.env.OPENVIKING_CREDENTIAL_SOURCE = "env"
      process.env.OPENVIKING_URL = "https://env.example.com"
      const project = join(dir, "Project A")
      await mkdir(join(project, ".git"), { recursive: true })
      await writeFile(join(project, ".git", "config"), '[remote "origin"]\n\turl = git@github.com:acme/project-a.git\n')

      const cfg = loadConfig(dir, project)
      assert.equal(cfg.effectivePeer.peerId, "github.com-acme-project-a")
      assert.equal(cfg.effectivePeer.source, "workspace")
      assert.equal(cfg.effectivePeer.origin, "{git_remote}")

      // Outside a repository the default derives nothing, so a scratch
      // directory writes to the user-level space instead of a peer of its own.
      const scratch = join(dir, "Scratch B")
      await mkdir(scratch, { recursive: true })
      const plain = loadConfig(dir, scratch)
      assert.equal(plain.effectivePeer.peerId, "")
      assert.equal(plain.effectivePeer.origin, "unresolved")
      assert.equal(plain.effectivePeer.legacyPeerId, scratch.replace(/[^A-Za-z0-9]/g, "-"))
    } finally {
      restoreOpenVikingEnv(snapshot)
    }
  })
})

test("loadConfig can disable workspace peer", async () => {
  const snapshot = { ...process.env }
  await withTempDir("ov-oc-ws-off-", async (dir) => {
    try {
      for (const key of Object.keys(process.env)) {
        if (key.startsWith("OPENVIKING_")) delete process.env[key]
      }
      process.env.OPENVIKING_CREDENTIAL_SOURCE = "env"
      process.env.OPENVIKING_URL = "https://env.example.com"
      process.env.OPENVIKING_WORKSPACE_PEER = "0"

      const cfg = loadConfig(dir, join(dir, "project"))
      assert.equal(cfg.effectivePeer.peerId, "")
      assert.equal(cfg.effectivePeer.source, "none")
    } finally {
      restoreOpenVikingEnv(snapshot)
    }
  })
})

test("loadConfig supports hook-only mode without registering the bundled MCP server", async () => {
  const snapshot = { ...process.env }
  await withTempDir("ov-oc-hook-only-", async (dir) => {
    try {
      for (const key of Object.keys(process.env)) {
        if (key.startsWith("OPENVIKING_")) delete process.env[key]
      }
      const project = join(dir, "project")
      await mkdir(join(project, ".opencode"), { recursive: true })
      await writeFile(join(project, ".opencode", "openviking-config.json"), JSON.stringify({
        mcp: { enabled: false },
      }))

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
        const configPath = join(dir, "openviking-config.json")
        await writeFile(configPath, JSON.stringify({
          mcp: { enabled: false },
          runtime: { dataDir: join(dir, "runtime") },
          repoContext: { enabled: false },
          autoRecall: { enabled: false },
          autoCapture: false,
        }))
        process.env.OPENVIKING_PLUGIN_CONFIG = configPath
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
      await mkdir(join(project, ".opencode"), { recursive: true })
      await writeFile(join(project, ".opencode", "openviking-config.json"), JSON.stringify({
        commitKeepRecentCount: 0,
      }))

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
      await mkdir(join(project, ".opencode"), { recursive: true })
      await writeFile(join(project, ".opencode", "openviking-config.json"), JSON.stringify({
        commitKeepRecentCount: null,
      }))

      const cfg = loadConfig(dir, project)
      assert.equal(cfg.commitKeepRecentCount, 10)
    } finally {
      restoreOpenVikingEnv(snapshot)
    }
  })
})

test("loadConfig falls back to config peerId when shared credentials define none (#4487)", async () => {
  const snapshot = { ...process.env }
  await withTempDir("ov-oc-peer-fallback-", async (dir) => {
    try {
      for (const key of Object.keys(process.env)) {
        if (key.startsWith("OPENVIKING_")) delete process.env[key]
      }
      const ovcli = join(dir, "ovcli.conf")
      const project = join(dir, "project")
      await mkdir(join(project, ".opencode"), { recursive: true })
      await writeFile(ovcli, JSON.stringify({
        url: "https://cli.example.com",
        api_key: "cli-key",
        account: "cli-account",
        user: "cli-user",
      }))
      await writeFile(join(project, ".opencode", "openviking-config.json"), JSON.stringify({
        enabled: true,
        peerId: "atomic-city",
        workspacePeer: false,
        recallPeerScope: "actor",
      }))
      process.env.OPENVIKING_CLI_CONFIG_FILE = ovcli

      const cfg = loadConfig(dir, project)
      assert.equal(cfg.peerId, "atomic-city")
      assert.deepEqual(cfg.effectivePeer, { peerId: "atomic-city", source: "explicit", origin: "explicit", legacyPeerId: "" })
      assert.equal(cfg.legacyCredentialsUsed, false)
    } finally {
      restoreOpenVikingEnv(snapshot)
    }
  })
})

test("loadConfig keeps ovcli actor_peer_id over config peerId", async () => {
  const snapshot = { ...process.env }
  await withTempDir("ov-oc-peer-cli-wins-", async (dir) => {
    try {
      for (const key of Object.keys(process.env)) {
        if (key.startsWith("OPENVIKING_")) delete process.env[key]
      }
      const ovcli = join(dir, "ovcli.conf")
      const project = join(dir, "project")
      await mkdir(join(project, ".opencode"), { recursive: true })
      await writeFile(ovcli, JSON.stringify({
        url: "https://cli.example.com",
        api_key: "cli-key",
        actor_peer_id: "cli-peer",
      }))
      await writeFile(join(project, ".opencode", "openviking-config.json"), JSON.stringify({
        peerId: "config-peer",
      }))
      process.env.OPENVIKING_CLI_CONFIG_FILE = ovcli

      const cfg = loadConfig(dir, project)
      assert.equal(cfg.peerId, "cli-peer")
      assert.deepEqual(cfg.effectivePeer, { peerId: "cli-peer", source: "explicit", origin: "explicit", legacyPeerId: "" })
    } finally {
      restoreOpenVikingEnv(snapshot)
    }
  })
})

test("loadConfig keeps env peer over config peerId when ovcli has none", async () => {
  const snapshot = { ...process.env }
  await withTempDir("ov-oc-peer-env-wins-", async (dir) => {
    try {
      for (const key of Object.keys(process.env)) {
        if (key.startsWith("OPENVIKING_")) delete process.env[key]
      }
      const ovcli = join(dir, "ovcli.conf")
      const project = join(dir, "project")
      await mkdir(join(project, ".opencode"), { recursive: true })
      await writeFile(ovcli, JSON.stringify({
        url: "https://cli.example.com",
        api_key: "cli-key",
      }))
      await writeFile(join(project, ".opencode", "openviking-config.json"), JSON.stringify({
        peerId: "config-peer",
      }))
      process.env.OPENVIKING_CLI_CONFIG_FILE = ovcli
      process.env.OPENVIKING_PEER_ID = "env-peer"

      const cfg = loadConfig(dir, project)
      assert.equal(cfg.peerId, "env-peer")
      assert.deepEqual(cfg.effectivePeer, { peerId: "env-peer", source: "explicit", origin: "explicit", legacyPeerId: "" })
    } finally {
      restoreOpenVikingEnv(snapshot)
    }
  })
})
