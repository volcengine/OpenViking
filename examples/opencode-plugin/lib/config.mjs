import fs from "fs"
import path from "path"
import { homedir } from "os"
import { resolveOpenVikingCredentials } from "./shared/credentials.mjs"
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs"

const DEFAULT_CONFIG = {
  endpoint: "http://127.0.0.1:1933",
  apiKey: "",
  account: "",
  user: "",
  peerId: "",
  workspacePeer: true,
  recallPeerScope: "all",
  enabled: true,
  timeoutMs: 30000,
  runtime: {
    dataDir: "",
  },
  repoContext: {
    enabled: true,
    cacheTtlMs: 60000,
  },
  autoRecall: {
    enabled: true,
    limit: 6,
    scoreThreshold: 0.35,
    maxContentChars: 500,
    preferAbstract: true,
    tokenBudget: 2000,
    minQueryLength: 3,
  },
  autoCapture: true,
  captureMode: "semantic",
  captureMaxLength: 24000,
  captureAssistantTurns: true,
  captureToolMaxChars: 2000,
  commitTokenThreshold: 20000,
  commitKeepRecentCount: 10,
  profileTokenBudget: 10000,
  resumeContextBudget: 32000,
  noAutoInject: false,
  bypassSession: false,
  bypassSessionPatterns: [],
  debug: false,
  debugLogPath: path.join(homedir(), ".openviking", "logs", "opencode-plugin.log"),
}

function cloneDefaultConfig() {
  return JSON.parse(JSON.stringify(DEFAULT_CONFIG))
}

function normalizeNumber(value, fallback, min, max) {
  const next = Number(value)
  if (!Number.isFinite(next)) return fallback
  return Math.max(min, Math.min(max, next))
}

function envBool(name) {
  const value = process.env[name]
  if (value == null || value === "") return undefined
  const lower = String(value).trim().toLowerCase()
  if (lower === "0" || lower === "false" || lower === "no" || lower === "off") return false
  if (lower === "1" || lower === "true" || lower === "yes" || lower === "on") return true
  return undefined
}

function str(value, fallback = "") {
  return typeof value === "string" && value.trim() ? value.trim() : fallback
}

function expandHome(value) {
  if (!value || typeof value !== "string") return value
  if (value === "~") return homedir()
  if (value.startsWith("~/") || value.startsWith("~\\")) return path.join(homedir(), value.slice(2))
  return value
}

function readConfigFile(pluginRoot, projectDirectory) {
  for (const configPath of getConfigPaths(pluginRoot, projectDirectory)) {
    try {
      if (!fs.existsSync(configPath)) continue
      return { path: configPath, data: JSON.parse(fs.readFileSync(configPath, "utf8")) }
    } catch (error) {
      console.warn(`Failed to load OpenViking config from ${configPath}:`, error)
    }
  }
  return { path: "", data: {} }
}

function getConfigPaths(pluginRoot, projectDirectory) {
  const paths = []
  if (process.env.OPENVIKING_PLUGIN_CONFIG) paths.push(expandHome(process.env.OPENVIKING_PLUGIN_CONFIG))
  if (projectDirectory) paths.push(path.join(projectDirectory, ".opencode", "openviking-config.json"))
  paths.push(path.join(homedir(), ".config", "opencode", "openviking-config.json"))
  paths.push(path.join(pluginRoot, "openviking-config.json"))
  return paths
}

function applyLegacyConnection(config, fileConfig) {
  const hasLegacyCredentials = ["endpoint", "apiKey", "account", "user", "peerId"]
    .some((key) => fileConfig[key] !== undefined && fileConfig[key] !== "")
  if (!hasLegacyCredentials) return false

  if (fileConfig.endpoint !== undefined) config.endpoint = fileConfig.endpoint
  if (fileConfig.apiKey !== undefined) config.apiKey = fileConfig.apiKey
  if (fileConfig.account !== undefined) config.account = fileConfig.account
  if (fileConfig.user !== undefined) config.user = fileConfig.user
  if (fileConfig.peerId !== undefined) config.peerId = fileConfig.peerId
  return true
}

function applyBehaviorConfig(config, fileConfig = {}) {
  if (fileConfig.enabled !== undefined) config.enabled = fileConfig.enabled !== false
  if (fileConfig.timeoutMs !== undefined) config.timeoutMs = fileConfig.timeoutMs
  config.runtime = {
    ...DEFAULT_CONFIG.runtime,
    ...(fileConfig.runtime ?? {}),
  }
  config.repoContext = {
    ...DEFAULT_CONFIG.repoContext,
    ...(fileConfig.repoContext ?? {}),
  }

  const autoRecall = fileConfig.autoRecall ?? {}
  config.autoRecall = {
    ...DEFAULT_CONFIG.autoRecall,
    ...autoRecall,
    enabled: autoRecall.enabled !== false,
    limit: autoRecall.limit ?? fileConfig.recallLimit ?? DEFAULT_CONFIG.autoRecall.limit,
    scoreThreshold: autoRecall.scoreThreshold ?? fileConfig.scoreThreshold ?? DEFAULT_CONFIG.autoRecall.scoreThreshold,
    maxContentChars: autoRecall.maxContentChars ?? fileConfig.recallMaxContentChars ?? DEFAULT_CONFIG.autoRecall.maxContentChars,
    preferAbstract: autoRecall.preferAbstract ?? fileConfig.recallPreferAbstract ?? DEFAULT_CONFIG.autoRecall.preferAbstract,
    tokenBudget: autoRecall.tokenBudget ?? fileConfig.recallTokenBudget ?? DEFAULT_CONFIG.autoRecall.tokenBudget,
    minQueryLength: autoRecall.minQueryLength ?? fileConfig.minQueryLength ?? DEFAULT_CONFIG.autoRecall.minQueryLength,
  }

  for (const key of [
    "autoCapture",
    "captureMode",
    "captureMaxLength",
    "captureAssistantTurns",
    "captureToolMaxChars",
    "commitTokenThreshold",
    "commitKeepRecentCount",
    "profileTokenBudget",
    "resumeContextBudget",
    "noAutoInject",
    "bypassSession",
    "bypassSessionPatterns",
    "debug",
    "debugLogPath",
    "workspacePeer",
    "recallPeerScope",
  ]) {
    if (fileConfig[key] !== undefined) config[key] = fileConfig[key]
  }
}

function applyEnv(config) {
  if (process.env.OPENVIKING_TIMEOUT_MS) config.timeoutMs = process.env.OPENVIKING_TIMEOUT_MS
  if (process.env.OPENVIKING_AUTO_RECALL !== undefined) {
    config.autoRecall.enabled = envBool("OPENVIKING_AUTO_RECALL") ?? config.autoRecall.enabled
  }
  if (process.env.OPENVIKING_RECALL_LIMIT) config.autoRecall.limit = process.env.OPENVIKING_RECALL_LIMIT
  if (process.env.OPENVIKING_SCORE_THRESHOLD) config.autoRecall.scoreThreshold = process.env.OPENVIKING_SCORE_THRESHOLD
  if (process.env.OPENVIKING_RECALL_MAX_CONTENT_CHARS) {
    config.autoRecall.maxContentChars = process.env.OPENVIKING_RECALL_MAX_CONTENT_CHARS
  }
  if (process.env.OPENVIKING_RECALL_TOKEN_BUDGET) config.autoRecall.tokenBudget = process.env.OPENVIKING_RECALL_TOKEN_BUDGET
  if (process.env.OPENVIKING_RECALL_PREFER_ABSTRACT !== undefined) {
    config.autoRecall.preferAbstract = envBool("OPENVIKING_RECALL_PREFER_ABSTRACT") ?? config.autoRecall.preferAbstract
  }
  if (process.env.OPENVIKING_RECALL_PEER_SCOPE) config.recallPeerScope = process.env.OPENVIKING_RECALL_PEER_SCOPE
  if (process.env.OPENVIKING_WORKSPACE_PEER !== undefined) {
    config.workspacePeer = envBool("OPENVIKING_WORKSPACE_PEER") ?? config.workspacePeer
  }
  if (process.env.OPENVIKING_MIN_QUERY_LENGTH) config.autoRecall.minQueryLength = process.env.OPENVIKING_MIN_QUERY_LENGTH
  if (process.env.OPENVIKING_AUTO_CAPTURE !== undefined) {
    config.autoCapture = envBool("OPENVIKING_AUTO_CAPTURE") ?? config.autoCapture
  }
  if (process.env.OPENVIKING_CAPTURE_MODE) config.captureMode = process.env.OPENVIKING_CAPTURE_MODE
  if (process.env.OPENVIKING_CAPTURE_MAX_LENGTH) config.captureMaxLength = process.env.OPENVIKING_CAPTURE_MAX_LENGTH
  if (process.env.OPENVIKING_CAPTURE_ASSISTANT_TURNS !== undefined) {
    config.captureAssistantTurns = envBool("OPENVIKING_CAPTURE_ASSISTANT_TURNS") ?? config.captureAssistantTurns
  }
  if (process.env.OPENVIKING_CAPTURE_TOOL_MAX_CHARS) {
    config.captureToolMaxChars = process.env.OPENVIKING_CAPTURE_TOOL_MAX_CHARS
  }
  if (process.env.OPENVIKING_COMMIT_TOKEN_THRESHOLD) {
    config.commitTokenThreshold = process.env.OPENVIKING_COMMIT_TOKEN_THRESHOLD
  }
  if (process.env.OPENVIKING_COMMIT_KEEP_RECENT_COUNT) {
    config.commitKeepRecentCount = process.env.OPENVIKING_COMMIT_KEEP_RECENT_COUNT
  }
  if (process.env.OPENVIKING_PROFILE_TOKEN_BUDGET) config.profileTokenBudget = process.env.OPENVIKING_PROFILE_TOKEN_BUDGET
  if (process.env.OPENVIKING_RESUME_CONTEXT_BUDGET) config.resumeContextBudget = process.env.OPENVIKING_RESUME_CONTEXT_BUDGET
  if (process.env.OPENVIKING_NO_AUTO_INJECT !== undefined) {
    config.noAutoInject = envBool("OPENVIKING_NO_AUTO_INJECT") ?? config.noAutoInject
  }
  if (process.env.OPENVIKING_BYPASS_SESSION !== undefined) {
    config.bypassSession = envBool("OPENVIKING_BYPASS_SESSION") ?? config.bypassSession
  }
  if (process.env.OPENVIKING_BYPASS_SESSION_PATTERNS) {
    config.bypassSessionPatterns = process.env.OPENVIKING_BYPASS_SESSION_PATTERNS
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean)
  }
  if (process.env.OPENVIKING_DEBUG !== undefined) config.debug = envBool("OPENVIKING_DEBUG") ?? config.debug
  if (process.env.OPENVIKING_DEBUG_LOG) config.debugLogPath = process.env.OPENVIKING_DEBUG_LOG
}

function normalizeConfig(config) {
  config.endpoint = str(config.endpoint, DEFAULT_CONFIG.endpoint).replace(/\/+$/, "")
  config.baseUrl = config.endpoint
  config.accountId = config.account
  config.userId = config.user
  config.timeoutMs = normalizeNumber(config.timeoutMs, DEFAULT_CONFIG.timeoutMs, 1000, 300000)
  config.repoContext.cacheTtlMs = normalizeNumber(
    config.repoContext.cacheTtlMs,
    DEFAULT_CONFIG.repoContext.cacheTtlMs,
    1000,
    60 * 60 * 1000,
  )
  config.autoRecall.limit = Math.max(1, Math.min(50, Math.round(Number(config.autoRecall.limit) || 6)))
  config.autoRecall.scoreThreshold = Math.max(0, Math.min(1, Number(config.autoRecall.scoreThreshold) || 0))
  config.autoRecall.maxContentChars = Math.max(100, Math.min(5000, Math.round(Number(config.autoRecall.maxContentChars) || 500)))
  config.autoRecall.tokenBudget = Math.max(200, Math.min(50000, Math.round(Number(config.autoRecall.tokenBudget) || 2000)))
  config.autoRecall.minQueryLength = Math.max(1, Math.min(64, Math.round(Number(config.autoRecall.minQueryLength) || 3)))
  config.captureMode = config.captureMode === "keyword" ? "keyword" : "semantic"
  config.recallPeerScope = config.recallPeerScope === "actor" ? "actor" : "all"
  config.captureMaxLength = Math.max(200, Math.min(100000, Math.round(Number(config.captureMaxLength) || 24000)))
  config.captureToolMaxChars = Math.max(200, Math.min(20000, Math.round(Number(config.captureToolMaxChars) || 2000)))
  config.commitTokenThreshold = Math.max(1000, Math.round(Number(config.commitTokenThreshold) || 20000))
  const rawCommitKeepRecentCount = config.commitKeepRecentCount
  const commitKeepRecentCount = rawCommitKeepRecentCount == null ||
    (typeof rawCommitKeepRecentCount === "string" && rawCommitKeepRecentCount.trim() === "")
    ? Number.NaN
    : Number(rawCommitKeepRecentCount)
  config.commitKeepRecentCount = Number.isFinite(commitKeepRecentCount)
    ? Math.max(0, Math.round(commitKeepRecentCount))
    : DEFAULT_CONFIG.commitKeepRecentCount
  config.profileTokenBudget = Math.max(500, Math.round(Number(config.profileTokenBudget) || 10000))
  config.resumeContextBudget = Math.max(1024, Math.round(Number(config.resumeContextBudget) || 32000))
  if (!Array.isArray(config.bypassSessionPatterns)) config.bypassSessionPatterns = []

  config.recallLimit = config.autoRecall.limit
  config.scoreThreshold = config.autoRecall.scoreThreshold
  config.recallMaxContentChars = config.autoRecall.maxContentChars
  config.recallPreferAbstract = config.autoRecall.preferAbstract !== false
  config.recallTokenBudget = config.autoRecall.tokenBudget
  config.minQueryLength = config.autoRecall.minQueryLength
  return config
}

export function loadConfig(pluginRoot, projectDirectory) {
  const config = cloneDefaultConfig()
  const { path: configPath, data: fileConfig } = readConfigFile(pluginRoot, projectDirectory)
  applyBehaviorConfig(config, fileConfig)

  const creds = resolveOpenVikingCredentials()
  config.endpoint = creds.baseUrl
  config.apiKey = creds.apiKey
  config.account = creds.account
  config.user = creds.user
  config.peerId = creds.peerId
  config.mcpUrl = creds.mcpUrl
  config.credentialSource = creds.credentialSource
  config.credentialPath = creds.cliPath || creds.ovPath || ""

  const mayUseLegacyCredentials = creds.credentialSource !== "env" &&
    creds.credentialSource !== "ovcli" &&
    !creds.apiKey &&
    !creds.account &&
    !creds.user &&
    !creds.peerId
  const legacyUsed = mayUseLegacyCredentials ? applyLegacyConnection(config, fileConfig) : false
  applyEnv(config)
  config.configPath = configPath
  config.legacyCredentialsUsed = legacyUsed && creds.credentialSource !== "env"
  const normalized = normalizeConfig(config)
  normalized.effectivePeer = resolveEffectivePeerId({ cfg: normalized, cwd: projectDirectory })
  return normalized
}

export function resolveDataDir(pluginRoot, config) {
  const configured = config.runtime?.dataDir
  if (configured) return expandHome(configured)
  return path.join(homedir(), ".config", "opencode", "openviking")
}
