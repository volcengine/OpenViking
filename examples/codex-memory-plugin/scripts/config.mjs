import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join, resolve as resolvePath } from "node:path"

const DEFAULT_CONFIG_PATH = join(homedir(), ".openviking", "ov.conf")
const DEFAULT_PLUGIN_HOME = join(homedir(), ".openviking", "codex-memory-plugin")
const DEFAULT_PLUGIN_CONFIG_PATH = join(DEFAULT_PLUGIN_HOME, "config.json")

function num(value, fallback) {
  if (typeof value === "number" && Number.isFinite(value)) return value
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return fallback
}

function str(value, fallback) {
  if (typeof value === "string" && value.trim()) return value.trim()
  return fallback
}

function normalizeMode(value) {
  const mode = str(value, "full")
  return mode === "recall_only" ? "recall_only" : "full"
}

export function loadConfig() {
  const configPath = resolvePath(
    (process.env.OPENVIKING_CONFIG_FILE || DEFAULT_CONFIG_PATH).replace(/^~/, homedir()),
  )

  let raw
  try {
    raw = readFileSync(configPath, "utf-8")
  } catch (err) {
    const msg = err?.code === "ENOENT"
      ? `Config file not found: ${configPath}\n  Create it from the example: cp ov.conf.example ~/.openviking/ov.conf`
      : `Failed to read config file: ${configPath} — ${err?.message || err}`
    process.stderr.write(`[openviking-memory] ${msg}\n`)
    process.exit(1)
  }

  let file
  try {
    file = JSON.parse(raw)
  } catch (err) {
    process.stderr.write(`[openviking-memory] Invalid JSON in ${configPath}: ${err?.message || err}\n`)
    process.exit(1)
  }

  const pluginConfigPath = resolvePath(
    str(process.env.OPENVIKING_CODEX_CONFIG_FILE, DEFAULT_PLUGIN_CONFIG_PATH).replace(/^~/, homedir()),
  )
  let pluginFile = {}
  try {
    pluginFile = JSON.parse(readFileSync(pluginConfigPath, "utf-8"))
  } catch (err) {
    if (err?.code !== "ENOENT") {
      process.stderr.write(`[openviking-memory] Invalid Codex plugin config in ${pluginConfigPath}: ${err?.message || err}\n`)
      process.exit(1)
    }
  }

  const server = file.server || {}
  const host = str(server.host, "127.0.0.1").replace("0.0.0.0", "127.0.0.1")
  const port = Math.floor(num(server.port, 1933))
  const baseUrl = `http://${host}:${port}`
  const apiKey = str(server.root_api_key, "")

  const codex = pluginFile || {}
  const pluginHome = resolvePath(
    str(process.env.OPENVIKING_CODEX_PLUGIN_HOME, DEFAULT_PLUGIN_HOME).replace(/^~/, homedir()),
  )
  const mode = normalizeMode(process.env.OPENVIKING_CODEX_MODE || codex.mode)
  const allowMemoryWrites = mode !== "recall_only"

  const timeoutMs = Math.max(1000, Math.floor(num(process.env.OPENVIKING_TIMEOUT_MS, num(codex.timeoutMs, 15000))))
  const captureTimeoutMs = Math.max(
    1000,
    Math.floor(num(process.env.OPENVIKING_CAPTURE_TIMEOUT_MS, num(codex.captureTimeoutMs, Math.max(timeoutMs * 2, 30000)))),
  )

  const debug = codex.debug === true || process.env.OPENVIKING_DEBUG === "1"
  const debugLogPath = str(
    process.env.OPENVIKING_DEBUG_LOG,
    join(homedir(), ".openviking", "logs", "codex-hooks.log"),
  )

  return {
    configPath,
    pluginConfigPath,
    pluginHome,
    captureStateDir: join(pluginHome, "state"),
    captureQueueDir: join(pluginHome, "queue"),
    hookDedupeDir: join(pluginHome, "state", "hook-dedupe"),
    factsPath: join(pluginHome, "state", "facts.json"),
    mode,
    allowMemoryWrites,
    baseUrl,
    apiKey,
    agentId: str(process.env.OPENVIKING_AGENT_ID, str(codex.agentId, "codex")),
    timeoutMs,
    autoRecall: codex.autoRecall !== false,
    recallLimit: Math.max(1, Math.floor(num(process.env.OPENVIKING_RECALL_LIMIT, num(codex.recallLimit, 1)))),
    scoreThreshold: Math.min(1, Math.max(0, num(process.env.OPENVIKING_SCORE_THRESHOLD, num(codex.scoreThreshold, 0.01)))),
    minQueryLength: Math.max(1, Math.floor(num(process.env.OPENVIKING_MIN_QUERY_LENGTH, num(codex.minQueryLength, 3)))),
    logRankingDetails: codex.logRankingDetails === true,
    searchAgentSkills: codex.searchAgentSkills === true,
    skipRecallOnWritePrompts: codex.skipRecallOnWritePrompts !== false,
    maxInjectedMemories: Math.max(1, Math.floor(num(codex.maxInjectedMemories, 1))),
    preferPromptLanguage: codex.preferPromptLanguage !== false,
    autoCapture: allowMemoryWrites && codex.autoCapture !== false,
    captureMode: codex.captureMode === "keyword"
      ? "keyword"
      : codex.captureMode === "semantic"
        ? "semantic"
        : "durable-facts",
    captureDispatch: str(codex.captureDispatch, "background") === "inline" ? "inline" : "background",
    captureMaxLength: Math.max(200, Math.floor(num(process.env.OPENVIKING_CAPTURE_MAX_LENGTH, num(codex.captureMaxLength, 24000)))),
    captureTimeoutMs,
    captureAssistantTurns: codex.captureAssistantTurns === true,
    debug,
    debugLogPath,
  }
}
