import { mkdir, opendir, open, stat, unlink } from "node:fs/promises"
import { createHash } from "node:crypto"
import { join } from "node:path"

const DEFAULT_TTL_MS = 7 * 24 * 60 * 60 * 1000

function normalizePart(value, fallback = "unknown") {
  const text = String(value || "").trim()
  if (!text) return fallback
  return text.replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 120) || fallback
}

function stableHash(parts) {
  const hash = createHash("sha256")
  for (const part of parts) hash.update(String(part || ""))
  return hash.digest("hex").slice(0, 16)
}

export function buildHookDedupeKey(eventName, input = {}) {
  const sessionId = normalizePart(input.session_id)
  const turnId = normalizePart(input.turn_id, "")
  if (turnId) return `${normalizePart(eventName)}__${sessionId}__${turnId}`

  if (eventName === "UserPromptSubmit") {
    return `${normalizePart(eventName)}__${sessionId}__${stableHash([
      input.prompt,
      input.cwd,
    ])}`
  }

  return `${normalizePart(eventName)}__${sessionId}__${stableHash([
    input.transcript_path,
    input.last_assistant_message,
    input.cwd,
  ])}`
}

function markerPath(dir, key) {
  return join(dir, `${key}.json`)
}

async function pruneOldMarkers(dir, now) {
  try {
    const handle = await opendir(dir)
    for await (const entry of handle) {
      if (!entry.isFile() || !entry.name.endsWith(".json")) continue
      const path = join(dir, entry.name)
      try {
        const info = await stat(path)
        if (now - info.mtimeMs > DEFAULT_TTL_MS) await unlink(path)
      } catch {}
    }
  } catch {}
}

export async function claimHookInvocation(dir, key, metadata = {}) {
  const now = Date.now()
  await mkdir(dir, { recursive: true })
  void pruneOldMarkers(dir, now)

  const path = markerPath(dir, key)
  let file
  try {
    file = await open(path, "wx")
  } catch (err) {
    if (err?.code === "EEXIST") return false
    throw err
  }

  try {
    await file.writeFile(JSON.stringify({
      key,
      createdAt: new Date(now).toISOString(),
      ...metadata,
    }))
  } finally {
    await file.close()
  }

  return true
}
