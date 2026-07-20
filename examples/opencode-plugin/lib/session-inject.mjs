import { buildProfileBlock } from "./shared/profile-inject.mjs"
import { effectivePeerId, fetchJSON, log } from "./utils.mjs"

export function createSessionInject({ config, sessionManager }) {
  const injectedSessions = new Set()

  async function injectSessionContext(input, output) {
    if (config.noAutoInject) return false
    const sessionID = input.sessionID ?? output.message?.sessionID
    const messageID = input.messageID ?? output.message?.id
    if (!sessionID || !messageID || injectedSessions.has(sessionID)) return false

    const ovSessionId = sessionManager.getMappedSessionId(sessionID)
    if (ovSessionId.includes("__subagent-")) return false

    const actorPeerId = effectivePeerId(config)
    const clientFetch = (endpoint, init = {}, options = {}) =>
      fetchJSON(config, endpoint, init, { ...options, actorPeerId, timeoutMs: 10000 })

    const parts = []
    const profile = await buildProfileBlock(clientFetch, config.profileTokenBudget, actorPeerId)
    if (profile?.block) parts.push(profile.block)

    const archive = await fetchArchiveBlock(clientFetch, ovSessionId, config.resumeContextBudget)
    if (archive) parts.push(archive)

    if (parts.length === 0) {
      injectedSessions.add(sessionID)
      return false
    }

    const block = [
      '<openviking-context source="session-start">',
      ...parts,
      "</openviking-context>",
    ].join("\n")

    output.parts.unshift({
      id: `prt-ov-session-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      type: "text",
      text: block,
      synthetic: true,
      sessionID,
      messageID,
    })
    injectedSessions.add(sessionID)
    log("INFO", "session-inject", "Injected OpenViking session context", {
      opencode_session: sessionID,
      openviking_session: ovSessionId,
      hasProfile: Boolean(profile?.block),
      hasArchive: Boolean(archive),
    })
    return true
  }

  return { injectSessionContext }
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
