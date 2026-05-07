import { strict as assert } from "node:assert"
import { mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { afterEach, beforeEach, describe, it } from "node:test"

import { loadClientConfig, resolveConfig } from "../servers/config.js"

const OV_CONF = {
  server: { host: "127.0.0.1", port: 1933, root_api_key: "ov-key" },
  default_account: "ov-account",
  default_user: "ov-user",
  default_agent: "ov-agent",
}

let tmpDir
let originalEnv

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "codex-memory-plugin-test-"))
  originalEnv = { ...process.env }
  delete process.env.OPENVIKING_CODEX_CONFIG_FILE
  delete process.env.OPENVIKING_AGENT_ID
  delete process.env.OPENVIKING_API_KEY
  delete process.env.OPENVIKING_ACCOUNT
  delete process.env.OPENVIKING_USER
  delete process.env.OPENVIKING_TIMEOUT_MS
  delete process.env.OPENVIKING_RECALL_LIMIT
  delete process.env.OPENVIKING_SCORE_THRESHOLD
})

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true })
  process.env = originalEnv
})

describe("loadClientConfig", () => {
  it("returns empty object when no client config exists", () => {
    process.env.OPENVIKING_CODEX_CONFIG_FILE = join(tmpDir, "missing.json")
    const result = loadClientConfig(process.env)
    assert.deepEqual(result, {})
  })

  it("reads agentId from client config when present", () => {
    const configPath = join(tmpDir, "config.json")
    writeFileSync(configPath, JSON.stringify({ agentId: "foo" }))
    process.env.OPENVIKING_CODEX_CONFIG_FILE = configPath

    const result = loadClientConfig(process.env)
    assert.equal(result.agentId, "foo")
  })
})

describe("resolveConfig precedence", () => {
  it("uses ov.conf default when no client config or env override is set", () => {
    const config = resolveConfig(OV_CONF, {}, {})
    assert.equal(config.agentId, "ov-agent")
    assert.equal(config.apiKey, "ov-key")
    assert.equal(config.accountId, "ov-account")
    assert.equal(config.userId, "ov-user")
  })

  it("falls back to literal defaults when ov.conf has no tenant fields set", () => {
    const config = resolveConfig({}, {}, {})
    assert.equal(config.agentId, "codex")
    assert.equal(config.accountId, "default")
    assert.equal(config.userId, "default")
  })

  it("client config overrides ov.conf default_agent", () => {
    const config = resolveConfig(OV_CONF, { agentId: "foo" }, {})
    assert.equal(config.agentId, "foo")
  })

  it("env var OPENVIKING_AGENT_ID overrides client config agentId", () => {
    const config = resolveConfig(OV_CONF, { agentId: "foo" }, { OPENVIKING_AGENT_ID: "bar" })
    assert.equal(config.agentId, "bar")
  })

  it("client config also overrides apiKey, accountId, userId, and numeric fields", () => {
    const config = resolveConfig(OV_CONF, {
      apiKey: "client-key",
      accountId: "client-account",
      userId: "client-user",
      timeoutMs: 5000,
      recallLimit: 12,
      scoreThreshold: 0.5,
    }, {})
    assert.equal(config.apiKey, "client-key")
    assert.equal(config.accountId, "client-account")
    assert.equal(config.userId, "client-user")
    assert.equal(config.timeoutMs, 5000)
    assert.equal(config.recallLimit, 12)
    assert.equal(config.scoreThreshold, 0.5)
  })

  it("env vars take precedence over client config for all fields", () => {
    const config = resolveConfig(OV_CONF, {
      apiKey: "client-key",
      agentId: "client-agent",
      timeoutMs: 5000,
    }, {
      OPENVIKING_API_KEY: "env-key",
      OPENVIKING_AGENT_ID: "env-agent",
      OPENVIKING_TIMEOUT_MS: "9000",
    })
    assert.equal(config.apiKey, "env-key")
    assert.equal(config.agentId, "env-agent")
    assert.equal(config.timeoutMs, 9000)
  })
})
