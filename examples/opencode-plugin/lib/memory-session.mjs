import fs from "fs"
import path from "path"
import {
  extractPartsFromPayload,
  extractTextFromPayload,
  shouldCaptureText,
} from "./shared/capture-utils.mjs"
import {
  deriveHarnessSessionId,
} from "./shared/session-model.mjs"
import {
  enqueue,
  replayPending,
} from "./shared/pending-queue.mjs"
import {
  sendSessionMessages,
} from "./shared/batch-send.mjs"
import {
  log,
  effectivePeerId,
  fetchJSON,
  safeStringify,
} from "./utils.mjs"

export function createMemorySessionManager({ config, pluginRoot }) {
  const sessions = new Map()
  const statePath = path.join(pluginRoot, "openviking-session-state.json")
  const oldSessionMapPath = path.join(pluginRoot, "openviking-session-map.json")
  let saveTimer = null

  async function init() {
    await migrateLegacySessionMap()
    await loadState()
    const health = await fetchJSON(config, "/health", {}, { timeoutMs: 5000 })
    if (health.ok) {
      await replayPending(
        (endpoint, init = {}, options = {}) => fetchJSON(config, endpoint, init, options),
        (stage, data) => log("DEBUG", "pending", stage, data),
      )
    }
  }

  async function loadState() {
    try {
      if (!fs.existsSync(statePath)) {
        log("INFO", "persistence", "No session state file found, starting fresh")
        return
      }
      const data = JSON.parse(await fs.promises.readFile(statePath, "utf8"))
      if (data.version !== 2) {
        log("ERROR", "persistence", "Unsupported session map version", { version: data.version })
        return
      }
      for (const [opencodeSessionId, persisted] of Object.entries(data.sessions ?? {})) {
        sessions.set(opencodeSessionId, deserializeSessionState(persisted))
      }
      log("INFO", "persistence", "Session state loaded", { count: sessions.size })
    } catch (error) {
      log("ERROR", "persistence", "Failed to load session state", { error: error?.message })
      if (fs.existsSync(statePath)) {
        await fs.promises.rename(statePath, `${statePath}.corrupted.${Date.now()}`)
      }
    }
  }

  async function saveState() {
    try {
      const persisted = {}
      for (const [opencodeSessionId, state] of sessions.entries()) {
        persisted[opencodeSessionId] = serializeSessionState(state)
      }
      const tempPath = `${statePath}.tmp`
      await fs.promises.writeFile(tempPath, JSON.stringify({ version: 2, sessions: persisted, lastSaved: Date.now() }, null, 2), "utf8")
      await fs.promises.rename(tempPath, statePath)
      log("DEBUG", "persistence", "Session state saved", { count: sessions.size })
    } catch (error) {
      log("ERROR", "persistence", "Failed to save session state", { error: error?.message })
    }
  }

  function debouncedSaveState() {
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(() => {
      saveState().catch((error) => {
        log("ERROR", "persistence", "Debounced save failed", { error: error?.message })
      })
    }, 300)
  }

  function serializeSessionState(state) {
    return {
      ovSessionId: state.ovSessionId,
      createdAt: state.createdAt,
      lastActivityAt: state.lastActivityAt,
      lastCommitTime: state.lastCommitTime,
      compactedAt: state.compactedAt,
      messages: Array.from(state.messages.entries()).map(([messageId, message]) => ([
        messageId,
        {
          role: message.role,
          captured: message.captured,
          parts: Array.from(message.parts.entries()),
        },
      ])),
    }
  }

  function deserializeSessionState(persisted) {
    return {
      ovSessionId: persisted.ovSessionId,
      createdAt: persisted.createdAt,
      lastActivityAt: persisted.lastActivityAt,
      lastCommitTime: persisted.lastCommitTime,
      compactedAt: persisted.compactedAt,
      messages: new Map((persisted.messages ?? []).map(([messageId, message]) => ([
        messageId,
        {
          role: message.role,
          captured: Boolean(message.captured),
          parts: new Map(message.parts ?? []),
        },
      ]))),
    }
  }

  function getMappedSessionId(opencodeSessionId) {
    return getOrCreateSession(opencodeSessionId).ovSessionId
  }

  async function handleEvent(event) {
    if (!event?.type || event.type === "session.diff") return

    if (event.type === "session.created") {
      await handleSessionCreated(event)
    } else if (event.type === "session.deleted") {
      await handleSessionDeleted(event)
    } else if (event.type === "session.error") {
      await handleSessionError(event)
    } else if (event.type === "session.compacted") {
      await handleSessionCompacted(event)
    } else if (event.type === "session.idle") {
      await handleSessionIdle(event)
    } else if (event.type === "message.updated") {
      await handleMessageUpdated(event)
    } else if (event.type === "message.part.updated") {
      await handleMessagePartUpdated(event)
    }
  }

  async function handleSessionCreated(event) {
    const sessionId = resolveEventSessionId(event)
    if (!sessionId) {
      log("ERROR", "event", "session.created event missing sessionId", { event: safeStringify(event) })
      return
    }
    const state = getOrCreateSession(sessionId, event)
    debouncedSaveState()
    const health = await fetchJSON(config, "/health", {}, { timeoutMs: 5000 })
    if (health.ok) {
      await replayPending(
        (endpoint, init = {}, options = {}) => fetchJSON(config, endpoint, init, options),
        (stage, data) => log("DEBUG", "pending", stage, data),
      )
    }
    log("INFO", "event", "OpenViking session derived", {
      opencode_session: sessionId,
      openviking_session: state.ovSessionId,
    })
  }

  async function handleSessionDeleted(event) {
    const sessionId = resolveEventSessionId(event)
    if (!sessionId) return
    await flushSession(sessionId, { commit: true, reason: event.type })
    sessions.delete(sessionId)
    await saveState()
  }

  async function handleSessionError(event) {
    const sessionId = resolveEventSessionId(event)
    if (!sessionId) return
    log("ERROR", "event", "OpenCode session error", { session_id: sessionId, error: safeStringify(event.error) })
    await handleSessionDeleted(event)
  }

  async function handleSessionCompacted(event) {
    await commitSessionBoundary(event, "session.compacted")
  }

  async function handleSessionIdle(event) {
    const sessionId = resolveEventSessionId(event)
    if (!sessionId) return
    await flushSession(sessionId, { commit: false, reason: "session.idle" })
  }

  async function commitSessionBoundary(event, reason) {
    const sessionId = resolveEventSessionId(event)
    if (!sessionId) return
    const state = getOrCreateSession(sessionId, event)
    state.compactedAt = Date.now()
    await flushSession(sessionId, { commit: true, reason })
  }

  async function handleMessageUpdated(event) {
    const message = event.properties?.info
    if (!message) return

    const sessionId = message.sessionID
    const messageId = message.id
    const role = message.role
    const finish = message.finish
    if (!sessionId || !messageId) return

    const state = getOrCreateSession(sessionId, event)
    const captured = state.messages.get(messageId)
    const next = captured ?? createMessageState()
    if (role === "user") {
      next.role = role
    } else if (role === "assistant") {
      next.role = role
    }
    state.messages.set(messageId, next)
    state.lastActivityAt = Date.now()
    debouncedSaveState()
  }

  async function handleMessagePartUpdated(event) {
    const part = event.properties?.part
    if (!part) return

    const sessionId = part.sessionID
    const messageId = part.messageID
    if (!sessionId || !messageId) return

    const state = getOrCreateSession(sessionId, event)
    const message = state.messages.get(messageId) ?? createMessageState()
    if (message.captured) return
    const partId = part.id ?? `${messageId}:${message.parts.size}`
    message.parts.set(partId, part)
    state.messages.set(messageId, message)
    state.lastActivityAt = Date.now()
    debouncedSaveState()
  }

  async function flushAll({ commit = false } = {}) {
    if (saveTimer) {
      clearTimeout(saveTimer)
      saveTimer = null
    }
    for (const sessionId of sessions.keys()) {
      await flushSession(sessionId, { commit, reason: "flushAll" })
    }
    await saveState()
  }

  async function flushSession(opencodeSessionId, { commit = false, reason = "manual" } = {}) {
    if (!opencodeSessionId) return false
    const state = sessions.get(opencodeSessionId)
    if (!state) return false

    const added = await flushPendingMessages(opencodeSessionId, state)
    if (commit) {
      await commitOvSession(state.ovSessionId, { force: true, reason })
    } else if (added > 0) {
      await maybeCommitByThreshold(state)
    }
    await saveState()
    return true
  }

  async function commitSession(sessionId, opencodeSessionId, abortSignal) {
    if (opencodeSessionId) {
      const state = sessions.get(opencodeSessionId)
      if (state) await flushPendingMessages(opencodeSessionId, state)
    }
    return commitOvSession(sessionId, { force: true, abortSignal, reason: "tool" })
  }

  return {
    init,
    handleEvent,
    getMappedSessionId,
    commitSession,
    flushAll,
    flushSession,
  }

  function createSessionState(opencodeSessionId, event = {}) {
    const parentId = event?.properties?.info?.parentID ?? event?.properties?.parentID ?? event?.parentID ?? ""
    const ovSessionId = parentId
      ? deriveHarnessSessionId("oc-", parentId, `subagent-${opencodeSessionId}`)
      : deriveHarnessSessionId("oc-", opencodeSessionId)
    return {
      ovSessionId,
      createdAt: Date.now(),
      lastActivityAt: Date.now(),
      lastCommitTime: undefined,
      compactedAt: undefined,
      messages: new Map(),
    }
  }

  function createMessageState() {
    return {
      role: "",
      parts: new Map(),
      captured: false,
    }
  }

  function getOrCreateSession(opencodeSessionId, event = {}) {
    let state = sessions.get(opencodeSessionId)
    if (!state) {
      state = createSessionState(opencodeSessionId, event)
      sessions.set(opencodeSessionId, state)
    }
    return state
  }

  function resolveEventSessionId(event) {
    return event?.properties?.info?.id ??
      event?.properties?.info?.sessionID ??
      event?.properties?.info?.sessionId ??
      event?.properties?.sessionID ??
      event?.properties?.sessionId ??
      event?.sessionID ??
      event?.sessionId ??
      event?.id
  }

  function resolvePartRole(part, fallbackRole) {
    if (fallbackRole) return fallbackRole
    const type = String(part?.type || part?.kind || "").toLowerCase()
    if (type.includes("tool") && type.includes("call")) return "assistant"
    if (type.includes("tool")) return "user"
    return ""
  }

  function buildCapturePayload(message) {
    const partsRaw = Array.from(message.parts.values())
    if (partsRaw.length === 0) return null
    const role = resolvePartRole(partsRaw[0], message.role)
    if (!role) return null
    if (role === "assistant" && !config.captureAssistantTurns) return null

    const rawText = partsRaw
      .map((part) => extractTextFromPayload(part, { toolMaxChars: config.captureToolMaxChars }))
      .filter(Boolean)
      .join("\n\n")
    const captureParts = partsRaw.flatMap((part) => extractPartsFromPayload(part, {
      toolMaxChars: config.captureToolMaxChars,
    }))
    const decision = shouldCaptureText(rawText, role, config)
    if (!decision.shouldCapture && captureParts.length === 0) return null
    const body = captureParts.length > 0
      ? { role, parts: captureParts }
      : { role, content: decision.text }
    const peerId = effectivePeerId(config)
    if (peerId) body.peer_id = peerId
    return body
  }

  async function flushPendingMessages(opencodeSessionId, state) {
    const toSend = []
    for (const [messageId, message] of state.messages.entries()) {
      if (message.captured) continue
      const body = buildCapturePayload(message)
      if (!body) {
        message.captured = true
        continue
      }
      toSend.push({ messageId, message, body })
    }
    if (toSend.length === 0) return 0

    let added = 0
    const health = await fetchJSON(config, "/health", {}, { timeoutMs: 5000 })
    if (!health.ok) {
      for (const item of toSend) {
        const queued = await enqueue("addMessage", state.ovSessionId, item.body)
        if (!queued.ok) break
        item.message.captured = true
        added += 1
      }
    } else {
      const res = await sendSessionMessages(
        (endpoint, init = {}, options = {}) => fetchJSON(config, endpoint, init, { timeoutMs: 10000, ...options }),
        state.ovSessionId,
        toSend.map((item) => item.body),
        { enqueueOnRetryable: true },
      )
      added = res.sent + res.queued
      for (const item of toSend.slice(0, added)) {
        item.message.captured = true
      }
      if (res.failed > 0 || res.enqueueFailed > 0) {
        log("ERROR", "message", "Failed to add message to OpenViking session", {
          openviking_session: state.ovSessionId,
          status: res.lastError?.status,
          error: res.lastError,
          failed: res.failed,
          enqueueFailed: res.enqueueFailed,
        })
      }
    }
    if (added > 0) {
      state.lastActivityAt = Date.now()
      debouncedSaveState()
    }
    return added
  }

  async function maybeCommitByThreshold(state) {
    if (config.commitTokenThreshold <= 0) return { committed: false }
    const meta = await fetchJSON(config, `/api/v1/sessions/${encodeURIComponent(state.ovSessionId)}`, {}, {
      timeoutMs: 5000,
    })
    const pendingTokens = Number(meta.result?.pending_tokens || 0)
    log("DEBUG", "session", "Pending token check", {
      openviking_session: state.ovSessionId,
      pendingTokens,
      threshold: config.commitTokenThreshold,
    })
    if (!meta.ok || pendingTokens < config.commitTokenThreshold) return { committed: false, pendingTokens }
    return commitOvSession(state.ovSessionId, { force: true, reason: "threshold" })
  }

  async function commitOvSession(ovSessionId, { force = false, reason = "manual", abortSignal } = {}) {
    if (!force && config.commitTokenThreshold <= 0) return { status: "skipped" }
    const body = { keep_recent_count: config.commitKeepRecentCount }
    const res = await fetchJSON(config, `/api/v1/sessions/${encodeURIComponent(ovSessionId)}/commit`, {
      method: "POST",
      body: JSON.stringify(body),
      signal: abortSignal,
    }, { timeoutMs: 30000 })
    if (res.ok) {
      for (const state of sessions.values()) {
        if (state.ovSessionId === ovSessionId) state.lastCommitTime = Date.now()
      }
      log("INFO", "session", "Committed OpenViking session", { openviking_session: ovSessionId, reason })
      return { status: "accepted", result: res.result }
    }
    if (isRetryableFailure(res)) {
      await enqueue("commitSession", ovSessionId, body)
      log("WARN", "session", "Queued OpenViking session commit", { openviking_session: ovSessionId, reason })
      return { status: "queued" }
    }
    throw new Error(`Failed to commit OpenViking session ${ovSessionId}: ${res.error?.message || res.status}`)
  }

  async function migrateLegacySessionMap() {
    if (!fs.existsSync(oldSessionMapPath)) return
    if (fs.existsSync(`${oldSessionMapPath}.migrated`)) return
    try {
      const data = JSON.parse(await fs.promises.readFile(oldSessionMapPath, "utf8"))
      const ovSessionIds = new Set()
      for (const persisted of Object.values(data.sessions ?? {})) {
        if (persisted?.ovSessionId) ovSessionIds.add(persisted.ovSessionId)
      }
      for (const ovSessionId of ovSessionIds) {
        try {
          await commitOvSession(ovSessionId, { force: true, reason: "legacy-migration" })
        } catch (error) {
          log("WARN", "migration", "Legacy orphan session commit failed", {
            openviking_session: ovSessionId,
            error: error?.message,
          })
        }
      }
      await fs.promises.rename(oldSessionMapPath, `${oldSessionMapPath}.migrated`)
      log("INFO", "migration", "Migrated legacy session map", { count: ovSessionIds.size })
    } catch (error) {
      log("ERROR", "migration", "Failed to migrate legacy session map", { error: error?.message })
    }
  }

  function isRetryableFailure(res) {
    if (!res || res.ok) return false
    const status = Number(res.status || 0)
    return !status || status >= 500 || status === 408 || status === 429
  }
}
