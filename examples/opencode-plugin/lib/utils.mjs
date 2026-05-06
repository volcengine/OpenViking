import fs from "fs"
import path from "path"
import { homedir } from "os"

export const DEFAULT_CONFIG = {
  endpoint: "http://localhost:1933",
  apiKey: "",
  account: "",
  user: "",
  agentId: "",
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
    scoreThreshold: 0.15,
    maxContentChars: 500,
    preferAbstract: true,
    tokenBudget: 2000,
  },
}

let logFilePath = null

function cloneDefaultConfig() {
  return JSON.parse(JSON.stringify(DEFAULT_CONFIG))
}

function mergeConfig(fileConfig = {}) {
  const config = cloneDefaultConfig()
  for (const key of ["endpoint", "apiKey", "account", "user", "agentId", "enabled", "timeoutMs"]) {
    if (fileConfig[key] !== undefined) config[key] = fileConfig[key]
  }
  config.runtime = {
    ...DEFAULT_CONFIG.runtime,
    dataDir: fileConfig.runtime?.dataDir ?? DEFAULT_CONFIG.runtime.dataDir,
  }
  config.repoContext = { ...DEFAULT_CONFIG.repoContext, ...(fileConfig.repoContext ?? {}) }
  config.autoRecall = { ...DEFAULT_CONFIG.autoRecall, ...(fileConfig.autoRecall ?? {}) }

  if (process.env.OPENVIKING_API_KEY) {
    config.apiKey = process.env.OPENVIKING_API_KEY
  }
  if (process.env.OPENVIKING_ACCOUNT) {
    config.account = process.env.OPENVIKING_ACCOUNT
  }
  if (process.env.OPENVIKING_USER) {
    config.user = process.env.OPENVIKING_USER
  }
  if (process.env.OPENVIKING_AGENT_ID) {
    config.agentId = process.env.OPENVIKING_AGENT_ID
  }

  config.timeoutMs = normalizeNumber(config.timeoutMs, DEFAULT_CONFIG.timeoutMs, 1000, 300000)
  config.repoContext.cacheTtlMs = normalizeNumber(
    config.repoContext.cacheTtlMs,
    DEFAULT_CONFIG.repoContext.cacheTtlMs,
    1000,
    60 * 60 * 1000,
  )
  clampRecallConfig(config.autoRecall)
  return config
}

function normalizeNumber(value, fallback, min, max) {
  const next = Number(value)
  if (!Number.isFinite(next)) return fallback
  return Math.max(min, Math.min(max, next))
}

function clampRecallConfig(recall) {
  recall.limit = Math.max(1, Math.min(50, Math.round(Number(recall.limit) || 6)))
  recall.scoreThreshold = Math.max(0, Math.min(1, Number(recall.scoreThreshold) || 0))
  recall.maxContentChars = Math.max(100, Math.min(5000, Math.round(Number(recall.maxContentChars) || 500)))
  recall.tokenBudget = Math.max(100, Math.min(10000, Math.round(Number(recall.tokenBudget) || 2000)))
}

export function loadConfig(pluginRoot, projectDirectory) {
  for (const configPath of getConfigPaths(pluginRoot, projectDirectory)) {
    try {
      if (fs.existsSync(configPath)) {
        const fileConfig = JSON.parse(fs.readFileSync(configPath, "utf8"))
        return mergeConfig(fileConfig)
      }
    } catch (error) {
      console.warn(`Failed to load OpenViking config from ${configPath}:`, error)
    }
  }
  return mergeConfig()
}

function getConfigPaths(pluginRoot, projectDirectory) {
  const paths = []
  if (process.env.OPENVIKING_PLUGIN_CONFIG) paths.push(expandHome(process.env.OPENVIKING_PLUGIN_CONFIG))
  if (projectDirectory) paths.push(path.join(projectDirectory, ".opencode", "openviking-config.json"))
  paths.push(path.join(homedir(), ".config", "opencode", "openviking-config.json"))
  paths.push(path.join(pluginRoot, "openviking-config.json"))
  return paths
}

export function resolveDataDir(pluginRoot, config) {
  const configured = config.runtime?.dataDir
  if (configured) return expandHome(configured)
  return path.join(homedir(), ".config", "opencode", "openviking")
}

function expandHome(value) {
  if (!value || typeof value !== "string") return value
  if (value === "~") return homedir()
  if (value.startsWith("~/") || value.startsWith("~\\")) return path.join(homedir(), value.slice(2))
  return value
}

export function initLogger(dataDir) {
  fs.mkdirSync(dataDir, { recursive: true })
  logFilePath = path.join(dataDir, "openviking-memory.log")
}

export function safeStringify(value) {
  if (value === null || value === undefined) return value
  if (typeof value !== "object") return value
  if (Array.isArray(value)) return value.map((item) => safeStringify(item))

  const result = {}
  for (const key of Object.keys(value)) {
    const item = value[key]
    if (typeof item === "function") {
      result[key] = "[Function]"
    } else if (typeof item === "object" && item !== null) {
      try {
        result[key] = safeStringify(item)
      } catch {
        result[key] = "[Circular or Non-serializable]"
      }
    } else {
      result[key] = item
    }
  }
  return result
}

export function log(level, toolName, message, data) {
  const normalizedLevel = String(level || "INFO").toUpperCase()
  const entry = {
    timestamp: new Date().toISOString(),
    level: normalizedLevel,
    tool: toolName,
    message,
    ...(data ? { data: safeStringify(data) } : {}),
  }

  if (!logFilePath) {
    if (normalizedLevel === "ERROR") console.error(message, data ?? "")
    return
  }

  try {
    fs.appendFileSync(logFilePath, `${JSON.stringify(entry)}\n`, "utf8")
  } catch (error) {
    console.error("Failed to write OpenViking plugin log:", error)
  }
}

export function makeToast(client) {
  return (message, variant = "warning") =>
    client?.tui?.showToast?.({
      body: { title: "OpenViking", message, variant, duration: 8000 },
    }).catch(() => {})
}

export function normalizeEndpoint(endpoint) {
  return endpoint.replace(/\/+$/, "")
}

export async function makeRequest(config, options) {
  const url = `${normalizeEndpoint(config.endpoint)}${options.endpoint}`
  const headers = makeAuthHeaders(config, { "Content-Type": "application/json", ...(options.headers ?? {}) })

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? config.timeoutMs)
  let onAbort = null

  if (options.abortSignal) {
    if (options.abortSignal.aborted) controller.abort()
    onAbort = () => controller.abort()
    options.abortSignal.addEventListener("abort", onAbort, { once: true })
  }

  try {
    const response = await fetch(url, {
      method: options.method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    })

    const text = await response.text()
    const payload = text ? parseJsonOrText(text) : {}

    if (!response.ok) {
      const rawError = typeof payload === "object" ? payload.error ?? payload.message : payload
      const errorMessage = typeof rawError === "string" ? rawError : JSON.stringify(rawError)
      if (response.status === 401 || response.status === 403) {
        throw new Error("Authentication failed. Please check apiKey/account/user in openviking-config.json or OPENVIKING_* environment variables.")
      }
      throw new Error(`Request failed (${response.status}): ${errorMessage}`)
    }

    return payload
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error(`Request timeout after ${options.timeoutMs ?? config.timeoutMs}ms`)
    }
    if (error?.message?.includes("fetch failed") || error?.code === "ECONNREFUSED") {
      throw new Error(`OpenViking service unavailable at ${config.endpoint}. Start it with: openviking-server --config ~/.openviking/ov.conf`)
    }
    throw error
  } finally {
    clearTimeout(timeout)
    if (options.abortSignal && onAbort) {
      options.abortSignal.removeEventListener("abort", onAbort)
    }
  }
}

export async function makeMultipartRequest(config, options) {
  const url = `${normalizeEndpoint(config.endpoint)}${options.endpoint}`
  const headers = makeAuthHeaders(config, options.headers ?? {})

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? config.timeoutMs)
  let onAbort = null

  if (options.abortSignal) {
    if (options.abortSignal.aborted) controller.abort()
    onAbort = () => controller.abort()
    options.abortSignal.addEventListener("abort", onAbort, { once: true })
  }

  try {
    const response = await fetch(url, {
      method: options.method,
      headers,
      body: options.body,
      signal: controller.signal,
    })

    const text = await response.text()
    const payload = text ? parseJsonOrText(text) : {}

    if (!response.ok) {
      const rawError = typeof payload === "object" ? payload.error ?? payload.message : payload
      const errorMessage = typeof rawError === "string" ? rawError : JSON.stringify(rawError)
      if (response.status === 401 || response.status === 403) {
        throw new Error("Authentication failed. Please check apiKey/account/user in openviking-config.json or OPENVIKING_* environment variables.")
      }
      throw new Error(`Request failed (${response.status}): ${errorMessage}`)
    }

    return payload
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error(`Request timeout after ${options.timeoutMs ?? config.timeoutMs}ms`)
    }
    if (error?.message?.includes("fetch failed") || error?.code === "ECONNREFUSED") {
      throw new Error(`OpenViking service unavailable at ${config.endpoint}. Start it with: openviking-server --config ~/.openviking/ov.conf`)
    }
    throw error
  } finally {
    clearTimeout(timeout)
    if (options.abortSignal && onAbort) {
      options.abortSignal.removeEventListener("abort", onAbort)
    }
  }
}

function makeAuthHeaders(config, headers = {}) {
  const result = { ...headers }
  if (config.apiKey) result["X-API-Key"] = config.apiKey
  if (config.account) result["X-OpenViking-Account"] = config.account
  if (config.user) result["X-OpenViking-User"] = config.user
  if (config.agentId) result["X-OpenViking-Agent"] = config.agentId
  return result
}

function parseJsonOrText(text) {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

export function getResponseErrorMessage(error) {
  if (!error) return "Unknown OpenViking error"
  if (typeof error === "string") return error
  return error.message || error.code || "Unknown OpenViking error"
}

export function unwrapResponse(response) {
  if (!response || typeof response !== "object") {
    throw new Error("OpenViking returned an invalid response")
  }
  if (response.status && response.status !== "ok") {
    throw new Error(getResponseErrorMessage(response.error))
  }
  return response.result
}

export function validateVikingUri(uri, toolName = "tool") {
  if (typeof uri !== "string" || !uri.startsWith("viking://")) {
    log("ERROR", toolName, "Invalid Viking URI", { uri })
    return 'Error: Invalid URI format. Must start with "viking://".'
  }
  return null
}

export function ensureRemoteUrl(value) {
  try {
    const url = new URL(value)
    return url.protocol === "http:" || url.protocol === "https:"
  } catch {
    return false
  }
}
