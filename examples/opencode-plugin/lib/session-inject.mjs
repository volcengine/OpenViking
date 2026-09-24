import { buildProfileBlock } from "./shared/profile-inject.mjs"
import { effectivePeerId, fetchJSON, log } from "./utils.mjs"

export function createSessionInject({ config, sessionManager }) {
  const injectedSessions = new Set()

  async function buildSessionContext(input) {
    if (config.noAutoInject) return false
    const sessionID = input.sessionID
    const messageID = input.messageID
    if (!sessionID || !messageID || injectedSessions.has(sessionID)) return false

    const ovSessionId = sessionManager.getMappedSessionId(sessionID)
    if (ovSessionId.includes("__subagent-")) return false

    const actorPeerId = effectivePeerId(config)
    const clientFetch = (endpoint, init = {}, options = {}) =>
      fetchJSON(config, endpoint, init, { ...options, actorPeerId, timeoutMs: 10000 })

    const parts = []
    const profile = await buildProfileBlock(clientFetch, config.profileTokenBudget, actorPeerId, config)
    if (profile?.block) parts.push(profile.block)

    const archive = await fetchArchiveBlock(clientFetch, ovSessionId, config.resumeContextBudget)
    if (archive) parts.push(archive)

    if (parts.length === 0) {
      injectedSessions.add(sessionID)
      return undefined
    }

    const block = [
      '<openviking-context source="session-start">',
      ...parts,
      "</openviking-context>",
    ].join("\n")

    injectedSessions.add(sessionID)
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
