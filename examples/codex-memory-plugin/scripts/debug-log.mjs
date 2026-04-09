import { appendFileSync, mkdirSync } from "node:fs"
import { dirname } from "node:path"
import { loadConfig } from "./config.mjs"

let cachedConfig

function cfg() {
  if (!cachedConfig) cachedConfig = loadConfig()
  return cachedConfig
}

function ensureDir(filePath) {
  try {
    mkdirSync(dirname(filePath), { recursive: true })
  } catch {}
}

function writeLine(filePath, value) {
  try {
    appendFileSync(filePath, `${JSON.stringify(value)}\n`)
  } catch {}
}

function localISO() {
  const now = new Date()
  const offset = now.getTimezoneOffset()
  const sign = offset <= 0 ? "+" : "-"
  const absolute = Math.abs(offset)
  const local = new Date(now.getTime() - offset * 60_000)
  return local.toISOString().replace(
    "Z",
    `${sign}${String(Math.floor(absolute / 60)).padStart(2, "0")}:${String(absolute % 60).padStart(2, "0")}`,
  )
}

const noop = () => {}

export function createLogger(hookName, overrideConfig) {
  const current = overrideConfig || cfg()
  if (!current.debug) return { log: noop, logError: noop }

  ensureDir(current.debugLogPath)

  function log(stage, data) {
    writeLine(current.debugLogPath, { ts: localISO(), hook: hookName, stage, data })
  }

  function logError(stage, err) {
    const error = err instanceof Error
      ? { message: err.message, stack: err.stack }
      : String(err)
    writeLine(current.debugLogPath, { ts: localISO(), hook: hookName, stage, error })
  }

  return { log, logError }
}
