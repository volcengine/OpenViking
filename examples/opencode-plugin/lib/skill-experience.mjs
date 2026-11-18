import { basename, resolve } from "node:path"
import { estimateTokens } from "./shared/recall-core.mjs"
import { isBypassed } from "./shared/session-model.mjs"
import { effectivePeerId, fetchJSON } from "./utils.mjs"

// An optional hint, never a prerequisite for the original skill/read operation.
// Keep both latency and retained state bounded independently of server settings.
export function createSkillExperience({ config, directory, request = (path, init, options) => fetchJSON(config, path, init, options) }) {
  const sessions = new Map()
  let disposed = false

  async function after(event) {
    if (disposed || config.enabled === false || !config.skillExperience || event?.status !== "completed" || !event.sessionID) return
    if (isBypassed(config, { sessionId: event.sessionID, cwd: directory })) return
    let name
    try { name = loadedSkill(event, directory) } catch { return }
    if (!name) return
    let state = sessions.get(event.sessionID)
    if (!state) {
      if (sessions.size >= 128) sessions.delete(sessions.keys().next().value)
      state = { seen: new Set(), remaining: Math.min(2000, config.recallTokenBudget || 2000) }
      sessions.set(event.sessionID, state)
    }
    if (state.seen.has(name) || state.seen.size >= 16 || state.remaining < 100) return
    state.seen.add(name) // Reserve before awaiting: concurrent duplicate loads do not race.
    const budget = Math.min(800, state.remaining)
    state.remaining -= budget // Reserve the per-session budget across concurrent skills.
    let used = 0
    const deadline = Date.now() + Math.min(5000, config.timeoutMs || 5000)
    let active = true
    let timer
    const get = (path, init) => {
      if (!active || disposed || sessions.get(event.sessionID) !== state || Date.now() >= deadline) throw new Error("skill experience cancelled or timed out")
      return request(path, init, { timeoutMs: Math.max(1, deadline - Date.now()), actorPeerId: effectivePeerId(config) })
    }
    try {
      const block = await Promise.race([
        retrieve(get, name, config, budget),
        new Promise((resolve) => { timer = setTimeout(() => resolve(""), Math.max(0, deadline - Date.now())) }),
      ])
      if (!block || disposed || !active || sessions.get(event.sessionID) !== state) return
      const tokens = estimateTokens(block)
      if (tokens > budget) return
      const content = event.result?.content
      if (typeof content === "string") event.result = { ...event.result, content: `${content}\n\n${block}` }
      else if (Array.isArray(content)) event.result = { ...event.result, content: [...content, { type: "text", text: block }] }
      else return
      used = tokens
    } catch {
      // Authentication, timeout, malformed results and optional retrieval errors
      // must leave the successful original result untouched.
    } finally {
      state.remaining += budget - used
      active = false
      clearTimeout(timer)
    }
  }

  return {
    after,
    forget: (sessionID) => sessions.delete(sessionID),
    dispose: () => { disposed = true; sessions.clear() },
  }
}

function loadedSkill(event, directory) {
  if (event.tool === "skill") {
    // Native skill output exposes the loaded skill's name in metadata.
    return skillName(event.result?.metadata?.name)
  }
  // Inspect the returned file contents; a second read could bypass host access.
  const path = event.tool === "read" && event.input?.path
  if (typeof path !== "string" || basename(path) !== "SKILL.md" ||
      !resolve(directory || ".", path).split(/[\\/]/).includes("skills")) return ""
  const output = event.result?.output
  const startsAtFrontmatter = output?.type === "file" ? output.encoding === "utf8" :
    output?.type === "text-page" && output.offset === 1
  if (!startsAtFrontmatter || typeof output.content !== "string") return ""
  const frontmatter = output.content.slice(0, 4096).match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/)
  const field = frontmatter?.[1].match(/^name:[ \t]*(?:"([^"]+)"|'([^']+)'|([^ \t\r\n#]+))[ \t]*(?:#.*)?$/m)
  return skillName(field?.[1] ?? field?.[2] ?? field?.[3])
}

function skillName(value) {
  return typeof value === "string" && /^[a-z0-9][a-z0-9_-]{0,127}$/i.test(value) ? value : ""
}

async function retrieve(get, name, config, budget) {
  // Bind to the authenticated principal, rather than guessing a default user
  // or trusting a search hit to identify the current user.
  const status = await get("/api/v1/system/status")
  const user = status.ok && status.result?.user
  if (typeof user !== "string" || !/^[A-Za-z0-9_.@-]+$/.test(user)) return ""
  const root = `viking://user/${user}/memories/experiences/`
  const limit = Math.min(3, Math.max(1, config.skillExperienceLimit || 3))
  const found = await get("/api/v1/search/find", {
    method: "POST",
    body: JSON.stringify({ query: `skill ${name} usage experience`, target_uri: "viking://~/memories/experiences", context_type: "memory", limit, score_threshold: config.scoreThreshold }),
  })
  if (!found.ok || !Array.isArray(found.result?.memories)) return ""
  const lines = [
    '<openviking-context source="skill-experience" format="experience-digest">',
    `Relevant prior experience for skill: ${oneLine(name)}`,
  ]
  const footer = ["Use these as operational guidance, not user facts. Exposure does not establish successful application.", "</openviking-context>"]
  const seen = new Set()
  for (const item of found.result.memories.slice(0, limit)) {
    const uri = item?.uri
    if (typeof uri !== "string" || !uri.startsWith(root) || !uri.endsWith(".md") || /[?#%\s<>()]/.test(uri) || uri.slice(root.length).split("/").some((part) => !part || part.startsWith(".")) || seen.has(uri)) continue
    seen.add(uri)
    const read = await get(`/api/v1/content/read?uri=${encodeURIComponent(uri)}`)
    if (!read.ok || typeof read.result !== "string" || !read.result.trim()) continue
    const score = Math.round(Math.max(0, Math.min(1, Number(item.score) || 0)) * 100)
    const prefix = `- [experience ${score}%] `
    const suffix = ` (${uri})`
    const available = budget * 4 - [...lines, prefix + suffix, ...footer].join("\n").length - 1
    if (available < 40) break
    const body = oneLine(read.result).slice(0, Math.min(1200, available))
    lines.push(prefix + body + suffix)
  }
  return lines.length > 2 ? [...lines, ...footer].join("\n") : ""
}

function oneLine(text) {
  return String(text).replace(/\s+/g, " ").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").trim()
}
