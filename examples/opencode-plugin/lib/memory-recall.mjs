import { log, makeRequest, unwrapResponse } from "./utils.mjs"

const AUTO_RECALL_TIMEOUT_MS = 5000
const RECALL_STOPWORDS = new Set([
  "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
  "did", "does", "is", "are", "was", "were", "the", "and", "for", "with",
  "from", "that", "this", "your", "you",
])
const RECALL_TOKEN_RE = /[a-z0-9]{2,}/gi
const PREFERENCE_QUERY_RE = /prefer|preference|favorite|favourite|like|偏好|喜欢|爱好|更倾向/i
const TEMPORAL_QUERY_RE = /when|what time|date|day|month|year|yesterday|today|tomorrow|last|next|什么时候|何时|哪天|几月|几年|昨天|今天|明天|上周|下周|上个月|下个月|去年|明年/i

export function createMemoryRecall({ config }) {
  async function injectRelevantMemories(input, output) {
    if (!config.autoRecall?.enabled) return
    const query = extractCurrentUserText(output.parts ?? [])
    if (!query) return

    const rawResults = await performRecallSearch(query)
    if (rawResults.length === 0) return

    const ranked = pickMemoriesForInjection(
      rawResults,
      config.autoRecall.limit,
      query,
      config.autoRecall.scoreThreshold,
    )
    if (ranked.length === 0) return

    const processed = postProcessMemories(
      ranked,
      config.autoRecall.maxContentChars,
      config.autoRecall.preferAbstract,
    )
    const block = formatMemoryBlock(processed, config.autoRecall.tokenBudget)
    if (!block) return

    if (prependSyntheticRecallPart(input, output, block)) {
      log("INFO", "recall", `Injected ${processed.length} memories`)
    }
  }

  async function performRecallSearch(query) {
    try {
      const response = await makeRequest(config, {
        method: "POST",
        endpoint: "/api/v1/search/find",
        body: { query: query.slice(0, 4000), limit: 20, mode: "auto" },
        timeoutMs: AUTO_RECALL_TIMEOUT_MS,
      })
      const result = unwrapResponse(response)
      return result?.memories ?? result?.results ?? []
    } catch {
      return []
    }
  }

  return { injectRelevantMemories }
}

function extractCurrentUserText(parts) {
  const texts = []
  for (const part of parts) {
    if (part.type !== "text" || typeof part.text !== "string") continue
    if (part.text.includes("<relevant-memories>")) return null
    if (!part.synthetic && !part.ignored) texts.push(part.text)
  }
  const joined = texts.join(" ").trim()
  return joined || null
}

function buildRecallQueryProfile(query) {
  const text = query.trim()
  const allTokens = text.toLowerCase().match(RECALL_TOKEN_RE) ?? []
  return {
    tokens: allTokens.filter((token) => !RECALL_STOPWORDS.has(token)),
    wantsPreference: PREFERENCE_QUERY_RE.test(text),
    wantsTemporal: TEMPORAL_QUERY_RE.test(text),
  }
}

function recallClampScore(value) {
  if (typeof value !== "number" || Number.isNaN(value)) return 0
  return Math.max(0, Math.min(1, value))
}

function lexicalOverlapBoost(tokens, text) {
  if (tokens.length === 0 || !text) return 0
  const haystack = ` ${text.toLowerCase()} `
  let matched = 0
  for (const token of tokens.slice(0, 8)) {
    if (haystack.includes(` ${token} `) || haystack.includes(token)) matched += 1
  }
  return Math.min(0.2, (matched / Math.min(tokens.length, 4)) * 0.2)
}

function isEventMemory(item) {
  const category = (item.category ?? "").toLowerCase()
  return category === "events" || item.uri?.includes("/events/")
}

function isPreferencesMemory(item) {
  return item.category === "preferences" || item.uri?.includes("/preferences/") || item.uri?.endsWith("/preferences")
}

function isLeafLikeMemory(item) {
  return item.level === 2 || item.is_leaf === true
}

function rankForInjection(item, query) {
  const baseScore = recallClampScore(item.score)
  const abstract = (item.abstract ?? item.overview ?? "").trim()
  const leafBoost = isLeafLikeMemory(item) ? 0.12 : 0
  const eventBoost = query.wantsTemporal && isEventMemory(item) ? 0.1 : 0
  const preferenceBoost = query.wantsPreference && isPreferencesMemory(item) ? 0.08 : 0
  const overlapBoost = lexicalOverlapBoost(query.tokens, `${item.uri} ${abstract}`)
  return baseScore + leafBoost + eventBoost + preferenceBoost + overlapBoost
}

function normalizeDedupeText(text) {
  return text.toLowerCase().replace(/\s+/g, " ").trim()
}

function isEventOrCaseMemory(item) {
  const category = (item.category ?? "").toLowerCase()
  const uri = (item.uri ?? "").toLowerCase()
  return category === "events" || category === "cases" || uri.includes("/events/") || uri.includes("/cases/")
}

function getMemoryDedupeKey(item) {
  const abstract = normalizeDedupeText(item.abstract ?? item.overview ?? "")
  const category = (item.category ?? "").toLowerCase() || "unknown"
  if (abstract && !isEventOrCaseMemory(item)) return `abstract:${category}:${abstract}`
  return `uri:${item.uri}`
}

function pickMemoriesForInjection(items, limit, queryText, scoreThreshold = 0) {
  const query = buildRecallQueryProfile(queryText)
  const sorted = [...items].sort((a, b) => rankForInjection(b, query) - rankForInjection(a, query))
  const deduped = []
  const seen = new Set()

  for (const item of sorted) {
    const key = getMemoryDedupeKey(item)
    if (seen.has(key)) continue
    seen.add(key)
    deduped.push(item)
  }

  const leaves = deduped.filter((item) => isLeafLikeMemory(item))
  if (leaves.length >= limit) return leaves.slice(0, limit)

  const picked = [...leaves]
  const used = new Set(leaves.map((item) => item.uri))
  for (const item of deduped) {
    if (picked.length >= limit) break
    if (used.has(item.uri)) continue
    if (recallClampScore(item.score) < scoreThreshold) continue
    picked.push(item)
  }
  return picked
}

function postProcessMemories(items, maxContentChars, preferAbstract) {
  return items.map((item) => {
    const abstract = (item.abstract ?? "").trim()
    const content = (item.content ?? "").trim()
    let displayContent = ""
    if (preferAbstract && abstract) displayContent = abstract
    else if (content) displayContent = content
    else if (abstract) displayContent = abstract
    if (displayContent.length > maxContentChars) displayContent = `${displayContent.slice(0, maxContentChars)}...`
    return { ...item, content: displayContent, abstract: abstract || undefined }
  })
}

function formatMemoryBlock(items, tokenBudget) {
  if (items.length === 0) return ""
  const maxBlockChars = tokenBudget * 4
  let usedChars = 0
  const lines = ["<relevant-memories>"]

  for (const item of items) {
    const title = item.title ? `${item.title}\n` : ""
    const content = item.content ?? ""
    const entry = `<memory uri="${item.uri}">\n${title}${content}\n</memory>`
    if (usedChars + entry.length + 1 > maxBlockChars) break
    lines.push(entry)
    usedChars += entry.length + 1
  }

  if (usedChars === 0) return ""
  lines.push("</relevant-memories>")
  lines.push('Use `memread` with a memory URI and level="overview" or level="read" for more details.')
  return lines.join("\n")
}

function prependSyntheticRecallPart(input, output, injection) {
  const sessionID = input.sessionID ?? output.message?.sessionID
  const messageID = input.messageID ?? output.message?.id
  if (!sessionID || !messageID) return false

  output.parts.unshift({
    id: `prt-ov-recall-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    type: "text",
    text: injection,
    synthetic: true,
    sessionID,
    messageID,
  })
  return true
}
