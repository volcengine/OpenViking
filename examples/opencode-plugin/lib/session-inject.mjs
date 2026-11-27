import { isCaptureEnabled } from "./shared/capture-utils.mjs"
import { buildProfileBlock } from "./shared/profile-inject.mjs"
import { formatSessionHistoryPointer } from "./shared/session-history-pointer.mjs"
import { effectivePeerId, fetchJSON, log } from "./utils.mjs"

export function createSessionInject({ config, sessionManager }) {
  // The compaction each session's start context was last sent after: a
  // compaction summarizes it away, so the next message sends it again.
  const injectedAfter = new Map()

  async function buildSessionContext(input) {
    if (sessionManager.isBypassed?.(input.sessionID)) return false
    const block = await buildStartContext(input)
    const pointer = historyPointer(input)
    return pointer ? [block, pointer].filter(Boolean).join("\n") : block
  }

  // The first message after a compaction. The plugin fills `session` only for
  // its bundled MCP server; in hook-only mode the model names it itself.
  function historyPointer(input) {
    if (!config.sessionHistoryPointer || !isCaptureEnabled(config)) return ""
    if (!input.sessionID || !input.messageID) return ""
    const ovSessionId = sessionManager.takeHistoryPointer?.(input.sessionID)
    if (!ovSessionId || ovSessionId.includes("__subagent-")) return ""
    log("INFO", "session-inject", "Injected OpenViking session-history pointer", {
      opencode_session: input.sessionID,
      openviking_session: ovSessionId,
    })
    return formatSessionHistoryPointer(ovSessionId, { sessionDefault: Boolean(config.mcp?.enabled) })
  }

  async function buildStartContext(input) {
    if (config.noAutoInject) return false
    const sessionID = input.sessionID
    const messageID = input.messageID
    const compactedAt = sessionManager.getCompactedAt?.(sessionID) ?? 0
    if (!sessionID || !messageID || injectedAfter.get(sessionID) === compactedAt) return false

    const ovSessionId = sessionManager.getMappedSessionId(sessionID)
    if (ovSessionId.includes("__subagent-")) return false

    const actorPeerId = effectivePeerId(config)
    const clientFetch = (endpoint, init = {}, options = {}) =>
      fetchJSON(config, endpoint, init, { ...options, actorPeerId, timeoutMs: 10000 })

    const parts = []
    const profile = await buildProfileBlock(clientFetch, config.profileTokenBudget, actorPeerId, config)
    if (profile?.block) parts.push(profile.block)

    const archive = config.resumeArchiveInject && !injectedAfter.has(sessionID)
      ? await fetchArchiveBlock(clientFetch, ovSessionId, config.resumeContextBudget)
      : ""
    if (archive) parts.push(archive)

    if (parts.length === 0) {
      injectedAfter.set(sessionID, compactedAt)
      return undefined
    }

    const block = [
      '<openviking-context source="session-start">',
      ...parts,
      "</openviking-context>",
    ].join("\n")

    injectedAfter.set(sessionID, compactedAt)
    log("INFO", "session-inject", "Injected OpenViking session context", {
      opencode_session: sessionID,
      openviking_session: ovSessionId,
      hasProfile: Boolean(profile?.block),
      hasArchive: Boolean(archive),
    })
    return block
  }

  async function injectSessionContext(input, output) {
    const block = await buildSessionContext({
      ...input,
      sessionID: input.sessionID ?? output.message?.sessionID,
      messageID: input.messageID ?? output.message?.id,
    })
    if (!block) return false
    output.parts.unshift({
      id: `prt-ov-session-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      type: "text",
      text: block,
      synthetic: true,
      sessionID: input.sessionID ?? output.message?.sessionID,
      messageID: input.messageID ?? output.message?.id,
    })
    return true
  }

  return { buildSessionContext, injectSessionContext }
}

const START_CONTEXT = /<openviking-context source="session-start">\n[\s\S]*?\n<\/openviking-context>\n?/

// A compaction can keep the message that carried the start context (its recent
// tail, an overflow replay), so a request keeps only the newest copy.
export function keepNewestStartContext(partLists) {
  const carriers = partLists.flatMap((parts) => (Array.isArray(parts) ? parts : [])
    .filter((part) => typeof part?.text === "string" && START_CONTEXT.test(part.text))
    .map((part) => [parts, part]))
  for (const [parts, part] of carriers.slice(0, -1)) {
    part.text = part.text.replace(START_CONTEXT, "")
    if (!part.text.trim()) parts.splice(parts.indexOf(part), 1)
  }
}

async function fetchArchiveBlock(fetcher, ovSessionId, tokenBudget) {
  const res = await fetcher(
    `/api/v1/sessions/${encodeURIComponent(ovSessionId)}/context?token_budget=${Math.max(1024, tokenBudget)}`,
  )
  if (!res.ok) return ""
  const overview = String(res.result?.latest_archive_overview || "").trim()
  if (!overview) return ""
  return [
    `<session-archive session="${ovSessionId}">`,
    overview,
    "</session-archive>",
  ].join("\n")
}
