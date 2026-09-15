import fs from "fs"
import path from "path"

import { createOvHttp } from "./shared/ov-http.mjs"

let logFilePath = null

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

export function effectivePeerId(config) {
  return String(config.effectivePeer?.peerId || config.peerId || "").trim() || null
}

// The config travels with every call here, so the client is built per call —
// a closure next to a network round-trip costs nothing.
function ovHttp(config) {
  return createOvHttp(
    { ...config, baseUrl: normalizeEndpoint(config.endpoint) },
    { defaultTimeoutMs: config.timeoutMs },
  )
}

export async function fetchJSON(config, endpoint, init = {}, options = {}) {
  return ovHttp(config)(endpoint, init, options)
}

/**
 * `fetchJSON` for the callers that would rather catch than branch: it resolves
 * to the unwrapped result and turns every failure into an Error whose message
 * is what the user can act on.
 */
export async function makeRequest(config, options) {
  const timeoutMs = options.timeoutMs ?? config.timeoutMs
  const response = await fetchJSON(config, options.endpoint, {
    method: options.method,
    headers: options.headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  }, { timeoutMs, actorPeerId: options.actorPeerId })
  if (response.ok) return response.result

  const message = getResponseErrorMessage(response.error)
  // Status 0 is the transport: nothing answered, so the message is ours, not
  // the server's, and it says what the user has to go fix.
  if (response.status === 0) {
    if (response.error?.aborted) {
      throw new Error(`Request timeout after ${timeoutMs}ms`)
    }
    if (message.includes("fetch failed") || message.includes("ECONNREFUSED")) {
      throw new Error(`OpenViking service unavailable at ${config.endpoint}. Start it with: openviking-server --config ~/.openviking/ov.conf`)
    }
    throw new Error(message)
  }
  if (response.status === 401 || response.status === 403) {
    throw new Error("Authentication failed. Check api_key/account/user in ~/.openviking/ovcli.conf, or the OPENVIKING_* environment variables.")
  }
  throw new Error(`Request failed (${response.status}): ${message}`)
}

export function getResponseErrorMessage(error) {
  if (!error) return "Unknown OpenViking error"
  if (typeof error === "string") return error
  return error.message || error.code || "Unknown OpenViking error"
}
