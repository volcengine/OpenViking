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
  let flushTimer = null
  let periodicFlushRunning = false
  let periodicFlushPromise = null
  let shuttingDown = false
  let savePromise = Promise.resolve()
  let saveCounter = 0

  async function init() {
    if (config.autoCapture) await migrateLegacySessionMap()
    await loadState()
    sweepStaleTempFiles()
    const health = await fetchJSON(config, "/health", {}, { timeoutMs: 5000 })
    if (health.ok) {
      await replayPending(
        (endpoint, init = {}, options = {}) => fetchJSON(config, endpoint, init, options),
        (stage, data) => log("DEBUG", "pending", stage, data),
      )
    }
    startPeriodicFlush()
  }

  function startPeriodicFlush() {
    if (!config.autoCapture) return
    if (shuttingDown) return
    const intervalMs = Math.max(10000, Number(config.periodicFlushIntervalMs) || 60000)
    if (flushTimer) clearInterval(flushTimer)
    flushTimer = setInterval(() => {
      // A tick may have been queued just before flushAll's clearInterval; skip it
      // during teardown so a periodic flush cannot start concurrently with flushAll.
      if (shuttingDown) return
      runPeriodicFlush().catch((error) => {
        log("ERROR", "session", "Periodic flush failed", { error: error?.message })
      })
    }, intervalMs)
    if (typeof flushTimer.unref === "function") flushTimer.unref()
    log("INFO", "session", "Periodic flush timer started", { intervalMs })
  }

  async function runPeriodicFlush() {
    if (periodicFlushRunning) return
    periodicFlushRunning = true
    const done = (async () => {
      try {
        for (const [opencodeSessionId, state] of sessions.entries()) {
          let hasPending = false
          for (const message of state.messages.values()) {
            if (!message.captured) {
              hasPending = true
              break
            }
          }
          if (hasPending) {
            await flushSession(opencodeSessionId, { commit: false, reason: "periodic" })
          }
        }
      } finally {
        periodicFlushRunning = false
      }
    })()
    periodicFlushPromise = done
    return done
  }

  function sweepStaleTempFiles() {
    // saveState writes to a unique temp file (`${statePath}.${pid}.${n}.tmp`) then
    // renames it onto statePath. A crash between writeFile and rename leaves an
    // orphan temp behind; sweep them on startup so they don't accumulate forever.
    try {
      const dir = path.dirname(statePath)
      const base = path.basename(statePath)
      for (const name of fs.readdirSync(dir)) {
        if (name.startsWith(`${base}.`) && name.endsWith(".tmp")) {
          try {
            fs.unlinkSync(path.join(dir, name))
          } catch {
            // best effort; ignore files removed by a concurrent process
          }
        }
      }
    } catch (error) {
      log("DEBUG", "persistence", "Temp sweep skipped", { error: error?.message })
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
    // Serialize saves within this process. The debounced saveTimer, flushSession
    // and runPeriodicFlush can all call saveState concurrently; without chaining,
    // two writes to the same temp path could interleave and corrupt the file that
    // then gets renamed onto the real state path.
    const run = savePromise.then(runSaveState, runSaveState)
    savePromise = run.catch(() => {})
    return run
  }

  async function runSaveState() {
    const tempPath = `${statePath}.${process.pid}.${saveCounter++}.tmp`
    try {
      const persisted = {}
      for (const [opencodeSessionId, state] of sessions.entries()) {
        persisted[opencodeSessionId] = serializeSessionState(state)
      }
      await fs.promises.writeFile(tempPath, JSON.stringify({ version: 2, sessions: persisted, lastSaved: Date.now() }, null, 2), "utf8")
      await fs.promises.rename(tempPath, statePath)
      log("DEBUG", "persistence", "Session state saved", { count: sessions.size })
    } catch (error) {
      log("ERROR", "persistence", "Failed to save session state", { error: error?.message })
      // Best-effort cleanup so a failed rename does not leave an orphan temp file.
      try {
        await fs.promises.unlink(tempPath)
      } catch {}
      // Rethrow so callers awaiting saveState() can observe persistent failures
      // (e.g. ENOSPC/EACCES). Fire-and-forget callers already attach a .catch.
      throw error
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
    } else if (event.type === "message.updated" && config.autoCapture) {
      await handleMessageUpdated(event)
    } else if (event.type === "message.part.updated" && config.autoCapture) {
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

  async function flushAll({ commit = false, shutdown = false } = {}) {
    if (shutdown) shuttingDown = true
    if (saveTimer) {
      clearTimeout(saveTimer)
      saveTimer = null
    }
    if (flushTimer) {
      clearInterval(flushTimer)
      flushTimer = null
    }
    // Drain any in-flight periodic flush so it cannot interleave with the loop
    // below and make commit ordering nondeterministic at teardown.
    if (periodicFlushPromise) {
      try {
        await periodicFlushPromise
      } catch {
        // periodic flush errors are already logged at their source
      }
    }
    for (const sessionId of sessions.keys()) {
      await flushSession(sessionId, { commit, reason: "flushAll" })
    }
    await saveState()
    // If this was not a shutdown, keep periodic flushing alive.
    if (!shutdown) startPeriodicFlush()
  }

  async function flushSession(opencodeSessionId, { commit = false, reason = "manual" } = {}) {
    if (!opencodeSessionId) return false
    const state = sessions.get(opencodeSessionId)
    if (!state) return false

    // Serialize overlapping flushes on the same session. flushPendingMessages
    // only marks messages captured=true after the network send resolves, so two
    // concurrent flushes (periodic timer + session.idle, or a slow flush the
    // timer did not await) would read the same captured=false batch and send it
    // twice. Chaining on state.flushing forces them to run one-after-another,
    // so each subsequent flush re-reads the post-send captured state.
    const run = async () => {
      const added = await flushPendingMessages(opencodeSessionId, state)
      if (commit && config.autoCapture) {
        await commitOvSession(state.ovSessionId, { force: true, reason })
      } else if (added > 0) {
        await maybeCommitByThreshold(state)
      }
      await saveState()
      return true
    }

    const previous = state.flushing || Promise.resolve()
    const current = previous.then(run, run)
    // Store the same promise the next flush will chain on. Swallow the rejection
    // on the gate branch (callers still observe it via `current`) so a failed run
    // does not become an unhandled rejection when nothing chains onto it. We do
    // NOT reset state.flushing afterwards: chaining on an already-settled promise
    // is effectively free, and an auto-reset opens a window where a rapidly queued
    // flush sees `undefined` and starts in parallel instead of serializing.
    state.flushing = current.catch(() => {})
    return current
  }

  async function commitSession(sessionId, opencodeSessionId, abortSignal) {
    if (opencodeSessionId) {
      // Route through flushSession so the send goes through the same
      // per-session serialization gate. Calling flushPendingMessages directly
      // would bypass state.flushing and could double-send a batch that a
      // concurrent periodic/idle flush is already sending.
      await flushSession(opencodeSessionId, { commit: false, reason: "tool" })
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
    if (!config.autoCapture) return 0
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
