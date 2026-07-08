import fs from "fs"
import path from "path"

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
  return String(config.peerId || "").trim() || null
}

export async function fetchJSON(config, endpoint, init = {}, options = {}) {
  const url = `${normalizeEndpoint(config.endpoint)}${endpoint}`
  const headers = makeAuthHeaders(
    config,
    { "Content-Type": "application/json", ...(init.headers ?? {}) },
    options.actorPeerId,
  )
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? config.timeoutMs)
  try {
    const response = await fetch(url, {
      ...init,
      headers,
      signal: controller.signal,
    })
    const text = await response.text()
    const payload = text ? parseJsonOrText(text) : {}
    if (!response.ok || payload?.status === "error") {
      return {
        ok: false,
        status: response.status,
        error: payload?.error || payload?.message || { message: `HTTP ${response.status}` },
      }
    }
    return { ok: true, status: response.status, result: payload?.result ?? payload }
  } catch (error) {
    return { ok: false, status: 0, error: { message: error?.message ?? String(error) } }
  } finally {
    clearTimeout(timeout)
  }
}

export async function makeRequest(config, options) {
  const url = `${normalizeEndpoint(config.endpoint)}${options.endpoint}`
  const headers = makeAuthHeaders(
    config,
    { "Content-Type": "application/json", ...(options.headers ?? {}) },
    options.actorPeerId,
  )

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
  const headers = makeAuthHeaders(config, options.headers ?? {}, options.actorPeerId)

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

function makeAuthHeaders(config, headers = {}, actorPeerId = "") {
  const result = { ...headers }
  if (config.apiKey) result["Authorization"] = `Bearer ${config.apiKey}`
  if (config.account) result["X-OpenViking-Account"] = config.account
  if (config.user) result["X-OpenViking-User"] = config.user
  const peerId = String(actorPeerId || "").trim()
  if (peerId) result["X-OpenViking-Actor-Peer"] = peerId
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
