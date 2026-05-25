import assert from "node:assert/strict"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { loadConfig, makeRequest } from "../lib/utils.mjs"

const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "openviking-opencode-plugin-"))
const pluginRoot = path.join(tempRoot, "plugin")
const configPath = path.join(tempRoot, "openviking-config.json")

fs.mkdirSync(pluginRoot, { recursive: true })

const originalEnv = {
  OPENVIKING_PLUGIN_CONFIG: process.env.OPENVIKING_PLUGIN_CONFIG,
  OPENVIKING_API_KEY: process.env.OPENVIKING_API_KEY,
  OPENVIKING_ACCOUNT: process.env.OPENVIKING_ACCOUNT,
  OPENVIKING_USER: process.env.OPENVIKING_USER,
  OPENVIKING_AGENT_ID: process.env.OPENVIKING_AGENT_ID,
  OPENVIKING_AGENT_ID_OVERRIDE: process.env.OPENVIKING_AGENT_ID_OVERRIDE,
}

function restoreEnv() {
  for (const [key, value] of Object.entries(originalEnv)) {
    if (value === undefined) {
      delete process.env[key]
    } else {
      process.env[key] = value
    }
  }
}

function resetEnv() {
  for (const key of Object.keys(originalEnv)) delete process.env[key]
  process.env.OPENVIKING_PLUGIN_CONFIG = configPath
}

function writeConfig(config) {
  fs.writeFileSync(configPath, JSON.stringify(config), "utf8")
}

function loadFor(directory) {
  return loadConfig(pluginRoot, directory)
}

try {
  resetEnv()
  writeConfig({ agentId: "shared-agent", projectIsolation: true })

  const alphaDir = path.join(tempRoot, "project-alpha")
  const betaDir = path.join(tempRoot, "project-beta")
  const alphaConfig = loadFor(alphaDir)
  const betaConfig = loadFor(betaDir)

  assert.notEqual(alphaConfig.agentId, betaConfig.agentId)
  assert.match(alphaConfig.agentId, /^shared-agent-project-alpha-[a-f0-9]{8}$/)
  assert.match(betaConfig.agentId, /^shared-agent-project-beta-[a-f0-9]{8}$/)
  assert.equal(loadFor(alphaDir).agentId, alphaConfig.agentId)

  writeConfig({ agentId: "shared-agent", projectIsolation: false })
  assert.equal(loadFor(alphaDir).agentId, "shared-agent")
  assert.equal(loadFor(betaDir).agentId, "shared-agent")

  writeConfig({ agentId: "shared-agent", projectIsolation: true })
  process.env.OPENVIKING_AGENT_ID = "env-agent"
  process.env.OPENVIKING_AGENT_ID_OVERRIDE = "override-agent"
  assert.equal(loadFor(alphaDir).agentId, "override-agent")
  assert.equal(loadFor(betaDir).agentId, "override-agent")

  delete process.env.OPENVIKING_AGENT_ID
  delete process.env.OPENVIKING_AGENT_ID_OVERRIDE
  assert.equal(loadFor(undefined).agentId, "shared-agent")

  const unicodeDir = path.join(tempRoot, "项目 alpha!")
  assert.match(loadFor(unicodeDir).agentId, /^shared-agent-alpha-[a-f0-9]{8}$/)

  const config = loadFor(alphaDir)
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (_url, options) => {
    assert.equal(options.headers["X-OpenViking-Agent"], config.agentId)
    return {
      ok: true,
      status: 200,
      text: async () => "{}",
    }
  }
  try {
    await makeRequest(config, { endpoint: "/api/v1/ping", method: "GET" })
  } finally {
    globalThis.fetch = originalFetch
  }
} finally {
  restoreEnv()
  fs.rmSync(tempRoot, { recursive: true, force: true })
}
