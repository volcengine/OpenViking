import test from "node:test"
import assert from "node:assert/strict"
import { mkdtemp, rm, writeFile, mkdir } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { loadConfig } from "../lib/config.mjs"

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
      assert.deepEqual(cfg.effectivePeer, { peerId: "env-peer", source: "explicit" })
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
      assert.deepEqual(cfg.effectivePeer, { peerId: "legacy-peer", source: "explicit" })
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

      const cfg = loadConfig(dir, project)
      assert.deepEqual(cfg.effectivePeer, {
        peerId: project.replace(/[^A-Za-z0-9]/g, "-"),
        source: "workspace",
      })
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
      assert.deepEqual(cfg.effectivePeer, { peerId: "", source: "none" })
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
