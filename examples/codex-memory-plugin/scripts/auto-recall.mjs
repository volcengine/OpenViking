#!/usr/bin/env node

import { loadConfig } from "./config.mjs"
import { createLogger } from "./debug-log.mjs"
import { findFactForPrompt } from "./fact-index.mjs"
import { buildHookDedupeKey, claimHookInvocation } from "./hook-dedupe.mjs"

const cfg = loadConfig()
const { log, logError } = createLogger("auto-recall")

function output(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`)
}

function approve(additionalContext) {
  const out = { continue: true }
  if (additionalContext) {
    out.hookSpecificOutput = {
      hookEventName: "UserPromptSubmit",
      additionalContext,
    }
  }
  output(out)
}

async function fetchJSON(path, init = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), cfg.timeoutMs)
  try {
    const headers = { "Content-Type": "application/json" }
    if (cfg.apiKey) headers["X-API-Key"] = cfg.apiKey
    if (cfg.agentId) headers["X-OpenViking-Agent"] = cfg.agentId
    const response = await fetch(`${cfg.baseUrl}${path}`, { ...init, headers, signal: controller.signal })
    const body = await response.json().catch(() => null)
    if (!response.ok || !body || body.status === "error") return null
    return body.result ?? body
  } catch {
    return null
  } finally {
    clearTimeout(timer)
  }
}

function clampScore(value) {
  if (typeof value !== "number" || Number.isNaN(value)) return 0
  return Math.max(0, Math.min(1, value))
}

const PREFERENCE_QUERY_RE = /prefer|preference|favorite|favourite|like|偏好|喜欢|爱好|更倾向/i
const TEMPORAL_QUERY_RE = /when|what time|date|day|month|year|yesterday|today|tomorrow|last|next|什么时候|何时|哪天|几月|几年|昨天|今天|明天/i
const QUERY_TOKEN_RE = /[a-z0-9\u4e00-\u9fa5]{2,}/gi
const STOPWORDS = new Set([
  "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
  "did", "does", "is", "are", "was", "were", "the", "and", "for", "with",
  "from", "that", "this", "your", "you", "my", "do", "not", "use", "any",
  "just", "answer", "reply", "directly", "future", "reference", "please",
  "tool", "tools", "without", "external", "once", "right", "now",
])
const MAX_INJECTED_ITEMS = 3
const MAX_SUMMARY_CHARS = 220
const OVERVIEW_URI_RE = /\/\.(?:overview|abstract)\.md$/i
const MEMORY_DOC_URI_RE = /\/mem_[^/]+\.md$/i
const CJK_CHAR_RE = /[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uac00-\ud7af]/
const WRITE_LIKE_PROMPT_RE = /for future reference|remember this|store this|save this|my [a-z0-9 _-]{2,80} is |update (?:my|the)|correct (?:my|the)|replace (?:my|the)|from now on/i

function buildQueryProfile(query) {
  const text = query
    .replace(/^do not use any tools?\.\s*/i, "")
    .replace(/^just (?:answer|reply) directly[:.]?\s*/i, "")
    .replace(/^for future reference,?\s*/i, "")
    .trim()
  const allTokens = text.toLowerCase().match(QUERY_TOKEN_RE) || []
  const tokens = allTokens.filter((token) => !STOPWORDS.has(token))
  return {
    tokens,
    wantsPreference: PREFERENCE_QUERY_RE.test(text),
    wantsTemporal: TEMPORAL_QUERY_RE.test(text),
  }
}

function lexicalOverlapBoost(tokens, text) {
  if (tokens.length === 0 || !text) return 0
  const haystack = ` ${text.toLowerCase()} `
  let matched = 0
  for (const token of tokens.slice(0, 8)) {
    if (haystack.includes(token)) matched += 1
  }
  return Math.min(0.2, (matched / Math.min(tokens.length, 4)) * 0.2)
}

function getRankingBreakdown(item, profile) {
  const baseScore = clampScore(item.score)
  const abstract = (item.abstract || item.overview || "").trim()
  const category = (item.category || "").toLowerCase()
  const uri = item.uri.toLowerCase()
  const leafBoost = (item.level === 2 || uri.endsWith(".md")) ? 0.12 : 0
  const eventBoost = profile.wantsTemporal && (category === "events" || uri.includes("/events/")) ? 0.1 : 0
  const preferenceBoost = profile.wantsPreference && (category === "preferences" || uri.includes("/preferences/")) ? 0.08 : 0
  const overlapBoost = lexicalOverlapBoost(profile.tokens, `${item.uri} ${abstract}`)
  return {
    baseScore,
    leafBoost,
    eventBoost,
    preferenceBoost,
    overlapBoost,
    finalScore: baseScore + leafBoost + eventBoost + preferenceBoost + overlapBoost,
  }
}

function dedupeByAbstract(items) {
  const seen = new Set()
  return items.filter((item) => {
    const key = (item.abstract || item.overview || "").trim().toLowerCase() || item.uri
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function isLeafMemoryItem(item) {
  if (!item?.uri) return false
  if (OVERVIEW_URI_RE.test(item.uri)) return false
  return item.level === 2 && MEMORY_DOC_URI_RE.test(item.uri)
}

function trimSummary(text) {
  const normalized = String(text || "")
    .replace(/\s+/g, " ")
    .trim()
  if (!normalized) return ""
  if (normalized.length <= MAX_SUMMARY_CHARS) return normalized
  return `${normalized.slice(0, MAX_SUMMARY_CHARS - 1).trim()}…`
}

function mostlyCjk(text) {
  const sample = String(text || "").replace(/\s+/g, "")
  if (!sample) return false
  let cjk = 0
  for (const char of sample) {
    if (CJK_CHAR_RE.test(char)) cjk += 1
  }
  return cjk / sample.length >= 0.4
}

function promptPrefersEnglish(prompt) {
  return !CJK_CHAR_RE.test(prompt)
}

function isWriteLikePrompt(prompt) {
  const normalized = String(prompt || "").trim()
  if (!normalized) return false
  if (/\?$/.test(normalized)) return false
  return WRITE_LIKE_PROMPT_RE.test(normalized)
}

function filterNearTop(items) {
  if (items.length === 0) return []
  const sorted = [...items].sort((left, right) => clampScore(right.score) - clampScore(left.score))
  const topScore = clampScore(sorted[0]?.score)
  const cutoff = topScore >= 0.6 ? topScore - 0.18 : Math.max(0.2, topScore * 0.75)
  return sorted.filter((item) => clampScore(item.score) >= cutoff)
}

function hasMeaningfulOverlap(item, profile) {
  if (!item) return false
  if (profile.tokens.length === 0) return true
  return lexicalOverlapBoost(profile.tokens, `${item.uri} ${item.abstract || item.overview || ""}`) > 0
}

function postProcess(items, limit, threshold) {
  const seen = new Set()
  const sorted = [...items].sort((left, right) => clampScore(right.score) - clampScore(left.score))
  const results = []
  for (const item of sorted) {
    if (clampScore(item.score) < threshold) continue
    const category = (item.category || "").toLowerCase() || "unknown"
    const abstract = (item.abstract || item.overview || "").trim().toLowerCase()
    const key = abstract ? `${category}:${abstract}` : `uri:${item.uri}`
    if (seen.has(key)) continue
    seen.add(key)
    results.push(item)
    if (results.length >= limit) break
  }
  return results
}

function pickItemsForInjection(items, limit, queryText) {
  if (items.length === 0 || limit <= 0) return []
  const profile = buildQueryProfile(queryText)
  const sorted = [...items]
    .map((item) => ({ item, breakdown: getRankingBreakdown(item, profile) }))
    .sort((left, right) => right.breakdown.finalScore - left.breakdown.finalScore)
    .map((entry) => entry.item)
  const deduped = dedupeByAbstract(sorted)
  const leafCandidates = filterNearTop(deduped.filter((item) => isLeafMemoryItem(item)))
  const overlappingLeaves = leafCandidates.filter((item) => hasMeaningfulOverlap(item, profile))
  const leaves = overlappingLeaves.length > 0 ? overlappingLeaves : leafCandidates.slice(0, 1)
  if (leaves.length >= limit) return leaves.slice(0, limit)

  const picked = [...leaves]
  const used = new Set(picked.map((item) => item.uri))
  for (const item of deduped) {
    if (picked.length >= limit) break
    if (used.has(item.uri)) continue
    if (!isLeafMemoryItem(item)) continue
    if (!hasMeaningfulOverlap(item, profile)) continue
    picked.push(item)
  }
  return picked
}

const USER_RESERVED_DIRS = new Set(["memories"])
const AGENT_RESERVED_DIRS = new Set(["memories", "skills", "instructions", "workspaces"])
const resolvedSpaces = {}

async function resolveScopeSpace(scope) {
  if (resolvedSpaces[scope]) return resolvedSpaces[scope]

  let fallbackSpace = "default"
  const status = await fetchJSON("/api/v1/system/status")
  if (status && typeof status.user === "string" && status.user.trim()) {
    fallbackSpace = status.user.trim()
  }

  const reserved = scope === "user" ? USER_RESERVED_DIRS : AGENT_RESERVED_DIRS
  const entries = await fetchJSON(`/api/v1/fs/ls?uri=${encodeURIComponent(`viking://${scope}`)}&output=original`)
  if (Array.isArray(entries)) {
    const spaces = entries
      .filter((entry) => entry?.isDir)
      .map((entry) => typeof entry.name === "string" ? entry.name.trim() : "")
      .filter((name) => name && !name.startsWith(".") && !reserved.has(name))

    if (spaces.length > 0) {
      if (spaces.includes(fallbackSpace)) {
        resolvedSpaces[scope] = fallbackSpace
        return fallbackSpace
      }
      if (scope === "user" && spaces.includes("default")) {
        resolvedSpaces[scope] = "default"
        return "default"
      }
      if (spaces.length === 1) {
        resolvedSpaces[scope] = spaces[0]
        return spaces[0]
      }
    }
  }

  resolvedSpaces[scope] = fallbackSpace
  return fallbackSpace
}

async function resolveTargetUri(targetUri) {
  const trimmed = targetUri.trim().replace(/\/+$/, "")
  const match = trimmed.match(/^viking:\/\/(user|agent)(?:\/(.*))?$/)
  if (!match) return trimmed

  const scope = match[1]
  const rawRest = (match[2] || "").trim()
  if (!rawRest) return trimmed

  const parts = rawRest.split("/").filter(Boolean)
  if (parts.length === 0) return trimmed

  const reserved = scope === "user" ? USER_RESERVED_DIRS : AGENT_RESERVED_DIRS
  if (!reserved.has(parts[0])) return trimmed

  const space = await resolveScopeSpace(scope)
  return `viking://${scope}/${space}/${parts.join("/")}`
}

async function searchScope(query, targetUri, limit) {
  const resolvedUri = await resolveTargetUri(targetUri)
  const result = await fetchJSON("/api/v1/search/find", {
    method: "POST",
    body: JSON.stringify({
      query,
      target_uri: resolvedUri,
      limit,
      score_threshold: 0,
    }),
  })

  if (!result) return []
  const collections = [result.memories, result.resources, result.skills]
  return collections.flatMap((items) => Array.isArray(items) ? items : [])
}

async function searchOpenViking(query, limit) {
  const targets = [
    "viking://user/memories",
    "viking://agent/memories",
  ]

  if (cfg.searchAgentSkills) {
    targets.push("viking://agent/skills")
  }

  const settled = await Promise.allSettled(targets.map((target) => searchScope(query, target, limit)))
  const all = settled
    .filter((result) => result.status === "fulfilled")
    .flatMap((result) => result.value)

  const seen = new Set()
  return all.filter((item) => {
    if (!item?.uri || seen.has(item.uri)) return false
    seen.add(item.uri)
    return true
  })
}

async function readContent(uri) {
  const result = await fetchJSON(`/api/v1/content/read?uri=${encodeURIComponent(uri)}`)
  if (typeof result === "string" && result.trim()) return result.trim()
  return null
}

async function markUsed(contexts) {
  const unique = [...new Set(contexts.filter((uri) => typeof uri === "string" && uri))]
  if (unique.length === 0) return

  try {
    const created = await fetchJSON("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({}),
    })
    const sessionId = created?.session_id
    if (!sessionId) return

    try {
      await fetchJSON(`/api/v1/sessions/${encodeURIComponent(sessionId)}/used`, {
        method: "POST",
        body: JSON.stringify({ contexts: unique }),
      })
      await fetchJSON(`/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`, {
        method: "POST",
        body: JSON.stringify({}),
      })
    } finally {
      await fetchJSON(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, {
        method: "DELETE",
      }).catch(() => {})
    }
  } catch {}
}

async function main() {
  if (!cfg.autoRecall) {
    log("skip", { stage: "init", reason: "autoRecall disabled" })
    approve()
    return
  }

  let input
  try {
    const chunks = []
    for await (const chunk of process.stdin) chunks.push(chunk)
    input = JSON.parse(Buffer.concat(chunks).toString())
  } catch {
    log("skip", { stage: "stdin_parse", reason: "invalid input" })
    approve()
    return
  }

  const prompt = (input.prompt || "").trim()
  const sessionId = input.session_id || "unknown"
  const turnId = input.turn_id || ""
  const dedupeKey = buildHookDedupeKey("UserPromptSubmit", input)
  log("start", {
    sessionId,
    turnId,
    query: prompt.slice(0, 200),
    queryLength: prompt.length,
    config: {
      recallLimit: cfg.recallLimit,
      scoreThreshold: cfg.scoreThreshold,
      searchAgentSkills: cfg.searchAgentSkills,
    },
  })

  let claimed
  try {
    claimed = await claimHookInvocation(cfg.hookDedupeDir, dedupeKey, {
      eventName: "UserPromptSubmit",
      sessionId,
      turnId,
      prompt: prompt.slice(0, 500),
    })
  } catch (err) {
    logError("dedupe_claim", err)
    approve()
    return
  }
  if (!claimed) {
    log("skip", { stage: "dedupe", reason: "already_processed", sessionId, turnId, dedupeKey })
    return
  }

  if (!prompt || prompt.length < cfg.minQueryLength) {
    log("skip", { stage: "query_check", reason: "query too short or empty" })
    approve()
    return
  }

  if (cfg.skipRecallOnWritePrompts && isWriteLikePrompt(prompt)) {
    log("skip", { stage: "query_check", reason: "write_like_prompt" })
    approve()
    return
  }

  const indexedFact = await findFactForPrompt(cfg.factsPath, prompt).catch(() => null)
  if (indexedFact?.sentence) {
    log("done", {
      selectedCount: 1,
      injectedCount: 1,
      source: "fact_index",
      familyKey: indexedFact.familyKey,
    })
    approve(`Relevant OpenViking memory: ${indexedFact.sentence}.`)
    return
  }

  const health = await fetchJSON("/health")
  if (!health) {
    logError("health_check", "server unreachable or unhealthy")
    approve()
    return
  }

  const candidateLimit = Math.max(cfg.recallLimit * 4, 20)
  const allItems = await searchOpenViking(prompt, candidateLimit)
  if (allItems.length === 0) {
    log("skip", { stage: "search", reason: "no results from any scope" })
    approve()
    return
  }

  const processed = postProcess(allItems, candidateLimit, cfg.scoreThreshold)
  const picked = pickItemsForInjection(
    processed,
    Math.min(cfg.recallLimit, cfg.maxInjectedMemories, MAX_INJECTED_ITEMS),
    prompt,
  )
  if (picked.length === 0) {
    log("skip", { stage: "post_process", reason: "no items survived thresholding" })
    approve()
    return
  }

  const englishOnly = promptPrefersEnglish(prompt)
  const rawLines = await Promise.all(
    picked.map(async (item) => {
      const full = await readContent(item.uri).catch(() => null)
      const summary = trimSummary(full || item.abstract || item.overview || "")
      if (!summary) return null
      if (cfg.preferPromptLanguage && englishOnly && mostlyCjk(summary)) return null
      return summary
    }),
  )

  const lines = rawLines
    .filter(Boolean)

  if (lines.length === 0) {
    log("skip", { stage: "format", reason: "no concise summaries survived filtering" })
    approve()
    return
  }

  void markUsed(picked.map((item) => item.uri))

  const additionalContext = lines.length === 1
    ? `Relevant OpenViking memory: ${lines[0]}`
    : `Relevant OpenViking memories:\n${lines.map((line) => `- ${line}`).join("\n")}`

  log("done", {
    selectedCount: picked.length,
    injectedCount: lines.length,
    uris: picked.map((item) => item.uri),
  })
  approve(additionalContext)
}

main().catch((err) => {
  logError("uncaught", err)
  approve()
})
