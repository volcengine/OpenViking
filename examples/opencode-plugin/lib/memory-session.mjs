import fs from "fs"
import path from "path"
import {
  log,
  makeRequest,
  safeStringify,
  unwrapResponse,
} from "./utils.mjs"

const MAX_BUFFERED_MESSAGES_PER_SESSION = 100
const BUFFERED_MESSAGE_TTL_MS = 15 * 60 * 1000
const BUFFER_CLEANUP_INTERVAL_MS = 30 * 1000
const COMMIT_WAIT_TIMEOUT_MS = 180000

export function createMemorySessionManager({ config, pluginRoot }) {
  const sessionMap = new Map()
  const sessionMessageBuffer = new Map()
  const commitWatchers = new Map()
  let sessionMapPath = path.join(pluginRoot, "openviking-session-map.json")
  let saveTimer = null
  let lastBufferCleanupAt = 0

  async function init() {
    await loadSessionMap()
    resumeBackgroundCommits()
  }

  async function loadSessionMap() {
    try {
      if (!fs.existsSync(sessionMapPath)) {
        log("INFO", "persistence", "No session map file found, starting fresh")
        return
      }
      const data = JSON.parse(await fs.promises.readFile(sessionMapPath, "utf8"))
      if (data.version !== 1) {
        log("ERROR", "persistence", "Unsupported session map version", { version: data.version })
        return
      }
      for (const [opencodeSessionId, persisted] of Object.entries(data.sessions ?? {})) {
        sessionMap.set(opencodeSessionId, deserializeSessionMapping(persisted))
      }
      log("INFO", "persistence", "Session map loaded", { count: sessionMap.size })
    } catch (error) {
      log("ERROR", "persistence", "Failed to load session map", { error: error?.message })
      if (fs.existsSync(sessionMapPath)) {
        await fs.promises.rename(sessionMapPath, `${sessionMapPath}.corrupted.${Date.now()}`)
      }
    }
  }

  async function saveSessionMap() {
    try {
      const sessions = {}
      for (const [opencodeSessionId, mapping] of sessionMap.entries()) {
        sessions[opencodeSessionId] = serializeSessionMapping(mapping)
      }
      const tempPath = `${sessionMapPath}.tmp`
      await fs.promises.writeFile(tempPath, JSON.stringify({ version: 1, sessions, lastSaved: Date.now() }, null, 2), "utf8")
      await fs.promises.rename(tempPath, sessionMapPath)
      log("DEBUG", "persistence", "Session map saved", { count: sessionMap.size })
    } catch (error) {
      log("ERROR", "persistence", "Failed to save session map", { error: error?.message })
    }
  }

  function debouncedSaveSessionMap() {
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(() => {
      saveSessionMap().catch((error) => {
        log("ERROR", "persistence", "Debounced save failed", { error: error?.message })
      })
    }, 300)
  }

  function serializeSessionMapping(mapping) {
    return {
      ovSessionId: mapping.ovSessionId,
      createdAt: mapping.createdAt,
      capturedMessages: Array.from(mapping.capturedMessages),
      messageRoles: Array.from(mapping.messageRoles.entries()),
      pendingMessages: Array.from(mapping.pendingMessages.entries()),
      lastCommitTime: mapping.lastCommitTime,
      commitInFlight: mapping.commitInFlight,
      commitTaskId: mapping.commitTaskId,
      commitStartedAt: mapping.commitStartedAt,
      pendingCleanup: mapping.pendingCleanup,
    }
  }

  function deserializeSessionMapping(persisted) {
    return {
      ovSessionId: persisted.ovSessionId,
      createdAt: persisted.createdAt,
      capturedMessages: new Set(persisted.capturedMessages ?? []),
      messageRoles: new Map(persisted.messageRoles ?? []),
      pendingMessages: new Map(persisted.pendingMessages ?? []),
      sendingMessages: new Set(),
      lastCommitTime: persisted.lastCommitTime,
      commitInFlight: persisted.commitInFlight,
      commitTaskId: persisted.commitTaskId,
      commitStartedAt: persisted.commitStartedAt,
      pendingCleanup: persisted.pendingCleanup,
    }
  }

  function getMappedSessionId(opencodeSessionId) {
    return sessionMap.get(opencodeSessionId)?.ovSessionId
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

    const ovSessionId = await ensureOpenVikingSession(sessionId)
    if (!ovSessionId) return

    const existing = sessionMap.get(sessionId)
    const mapping = existing ?? createSessionMapping(ovSessionId)
    mapping.ovSessionId = ovSessionId
    sessionMap.set(sessionId, mapping)

    const bufferedMessages = sessionMessageBuffer.get(sessionId)
    if (bufferedMessages?.length) {
      for (const buffered of bufferedMessages) {
        if (buffered.role) mapping.messageRoles.set(buffered.messageId, buffered.role)
        if (buffered.content) {
          mapping.pendingMessages.set(
            buffered.messageId,
            mergeMessageContent(mapping.pendingMessages.get(buffered.messageId), buffered.content),
          )
        }
      }
      sessionMessageBuffer.delete(sessionId)
      await flushPendingMessages(sessionId, mapping)
    }

    debouncedSaveSessionMap()
    log("INFO", "event", "Session mapping established", {
      opencode_session: sessionId,
      openviking_session: ovSessionId,
    })
  }

  async function handleSessionDeleted(event) {
    const sessionId = resolveEventSessionId(event)
    if (!sessionId) return

    const mapping = sessionMap.get(sessionId)
    if (!mapping) {
      sessionMessageBuffer.delete(sessionId)
      return
    }

    await flushPendingMessages(sessionId, mapping)
    if (mapping.capturedMessages.size > 0 || mapping.commitInFlight) {
      mapping.pendingCleanup = true
      if (!mapping.commitInFlight) await startBackgroundCommit(mapping, sessionId)
    } else {
      sessionMap.delete(sessionId)
      sessionMessageBuffer.delete(sessionId)
      await saveSessionMap()
    }
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

  async function commitSessionBoundary(event, reason) {
    const sessionId = resolveEventSessionId(event)
    if (!sessionId) return

    const mapping = sessionMap.get(sessionId)
    if (!mapping) return

    await flushPendingMessages(sessionId, mapping)
    if (mapping.commitInFlight) {
      monitorBackgroundCommit(mapping, sessionId)
      return
    }
    if (mapping.capturedMessages.size > 0) {
      log("INFO", "session", "Committing OpenViking session at lifecycle boundary", {
        opencode_session: sessionId,
        openviking_session: mapping.ovSessionId,
        reason,
      })
      await startBackgroundCommit(mapping, sessionId)
    }
  }

  async function handleMessageUpdated(event) {
    const message = event.properties?.info
    if (!message) return

    const sessionId = message.sessionID
    const messageId = message.id
    const role = message.role
    const finish = message.finish
    if (!sessionId || !messageId) return

    const mapping = sessionMap.get(sessionId)
    if (!mapping) {
      upsertBufferedMessage(sessionId, messageId, role ? { role } : {})
      return
    }

    if (role === "user") {
      mapping.messageRoles.set(messageId, role)
    } else if (role === "assistant" && finish === "stop") {
      mapping.messageRoles.set(messageId, role)
    }

    await flushPendingMessages(sessionId, mapping)
  }

  async function handleMessagePartUpdated(event) {
    const part = event.properties?.part
    if (!part) return

    const sessionId = part.sessionID
    const messageId = part.messageID
    if (!sessionId || !messageId || part.type !== "text" || !part.text?.trim()) return

    const mapping = sessionMap.get(sessionId)
    if (!mapping) {
      upsertBufferedMessage(sessionId, messageId, { content: part.text })
      return
    }

    if (mapping.capturedMessages.has(messageId)) return
    mapping.pendingMessages.set(messageId, mergeMessageContent(mapping.pendingMessages.get(messageId), part.text))
  }

  async function ensureOpenVikingSession(opencodeSessionId) {
    const knownSessionId = sessionMap.get(opencodeSessionId)?.ovSessionId
    if (knownSessionId) {
      try {
        const response = await makeRequest(config, {
          method: "GET",
          endpoint: `/api/v1/sessions/${encodeURIComponent(knownSessionId)}`,
          timeoutMs: 5000,
        })
        if (unwrapResponse(response)) return knownSessionId
      } catch (error) {
        log("INFO", "session", "Persisted OpenViking session unavailable, creating a new one", {
          opencode_session: opencodeSessionId,
          openviking_session: knownSessionId,
          error: error?.message,
        })
      }
    }

    try {
      const response = await makeRequest(config, {
        method: "POST",
        endpoint: "/api/v1/sessions",
        body: {},
        timeoutMs: 5000,
      })
      const sessionId = unwrapResponse(response)?.session_id
      if (!sessionId) throw new Error("OpenViking did not return a session_id")
      return sessionId
    } catch (error) {
      log("ERROR", "session", "Failed to create OpenViking session", {
        opencode_session: opencodeSessionId,
        error: error?.message,
      })
      return null
    }
  }

  async function flushPendingMessages(opencodeSessionId, mapping) {
    if (mapping.commitInFlight) return

    for (const messageId of Array.from(mapping.pendingMessages.keys())) {
      if (mapping.capturedMessages.has(messageId) || mapping.sendingMessages.has(messageId)) continue
      const role = mapping.messageRoles.get(messageId)
      const content = mapping.pendingMessages.get(messageId)
      if (!role || !content?.trim()) continue

      mapping.sendingMessages.add(messageId)
      try {
        const success = await addMessageToSession(mapping.ovSessionId, role, content)
        if (success) {
          const latest = mapping.pendingMessages.get(messageId)
          if (latest && latest !== content) {
            continue
          }
          mapping.pendingMessages.delete(messageId)
          mapping.capturedMessages.add(messageId)
          debouncedSaveSessionMap()
        }
      } finally {
        mapping.sendingMessages.delete(messageId)
      }
    }
  }

  async function addMessageToSession(ovSessionId, role, content) {
    try {
      const response = await makeRequest(config, {
        method: "POST",
        endpoint: `/api/v1/sessions/${encodeURIComponent(ovSessionId)}/messages`,
        body: { role, content },
        timeoutMs: 5000,
      })
      unwrapResponse(response)
      return true
    } catch (error) {
      log("ERROR", "message", "Failed to add message to OpenViking session", {
        openviking_session: ovSessionId,
        role,
        error: error?.message,
      })
      return false
    }
  }

  async function startBackgroundCommit(mapping, opencodeSessionId, abortSignal) {
    if (mapping.commitInFlight && mapping.commitTaskId) {
      if (!abortSignal) monitorBackgroundCommit(mapping, opencodeSessionId)
      return { mode: "background", taskId: mapping.commitTaskId }
    }

    try {
      const response = await makeRequest(config, {
        method: "POST",
        endpoint: `/api/v1/sessions/${encodeURIComponent(mapping.ovSessionId)}/commit`,
        timeoutMs: 10000,
        abortSignal,
      })
      const result = unwrapResponse(response)
      const taskId = result?.task_id

      if (!taskId) {
        await finalizeCommitSuccess(mapping, opencodeSessionId)
        return { mode: "completed", result }
      }

      mapping.commitInFlight = true
      mapping.commitTaskId = taskId
      mapping.commitStartedAt = Date.now()
      debouncedSaveSessionMap()
      if (!abortSignal) monitorBackgroundCommit(mapping, opencodeSessionId)
      return { mode: "background", taskId }
    } catch (error) {
      if (error?.message?.includes("already has a commit in progress")) {
        const taskId = await findRunningCommitTaskId(mapping.ovSessionId)
        if (taskId) {
          mapping.commitInFlight = true
          mapping.commitTaskId = taskId
          mapping.commitStartedAt = mapping.commitStartedAt ?? Date.now()
          debouncedSaveSessionMap()
          if (!abortSignal) monitorBackgroundCommit(mapping, opencodeSessionId)
          return { mode: "background", taskId }
        }
      }
      log("ERROR", "session", "Failed to start OpenViking commit", {
        openviking_session: mapping.ovSessionId,
        opencode_session: opencodeSessionId,
        error: error?.message,
      })
      return null
    }
  }

  async function waitForCommitCompletion(mapping, opencodeSessionId, abortSignal, timeoutMs = COMMIT_WAIT_TIMEOUT_MS) {
    const startedAt = Date.now()
    while (Date.now() - startedAt < timeoutMs) {
      if (abortSignal?.aborted) throw new Error("Operation aborted")
      if (!mapping.commitInFlight) return null
      if (!mapping.commitTaskId) {
        mapping.commitTaskId = await findRunningCommitTaskId(mapping.ovSessionId)
        if (!mapping.commitTaskId) {
          clearCommitState(mapping)
          debouncedSaveSessionMap()
          return null
        }
      }

      const task = await getTask(mapping.commitTaskId, abortSignal)
      if (task.status === "completed") {
        await finalizeCommitSuccess(mapping, opencodeSessionId)
        return task
      }
      if (task.status === "failed") {
        clearCommitState(mapping)
        debouncedSaveSessionMap()
        throw new Error(task.error || "Background commit failed")
      }
      await sleep(2000, abortSignal)
    }
    return null
  }

  async function getTask(taskId, abortSignal) {
    const response = await makeRequest(config, {
      method: "GET",
      endpoint: `/api/v1/tasks/${encodeURIComponent(taskId)}`,
      timeoutMs: 5000,
      abortSignal,
    })
    return unwrapResponse(response)
  }

  async function findRunningCommitTaskId(ovSessionId) {
    try {
      const response = await makeRequest(config, {
        method: "GET",
        endpoint: `/api/v1/tasks?task_type=session_commit&resource_id=${encodeURIComponent(ovSessionId)}&limit=10`,
        timeoutMs: 5000,
      })
      const tasks = unwrapResponse(response) ?? []
      return tasks.find((task) => task.status === "pending" || task.status === "running")?.task_id
    } catch (error) {
      log("WARN", "session", "Failed to query running commit tasks", { error: error?.message })
      return undefined
    }
  }

  async function finalizeCommitSuccess(mapping, opencodeSessionId) {
    mapping.lastCommitTime = Date.now()
    mapping.capturedMessages.clear()
    clearCommitState(mapping)
    debouncedSaveSessionMap()

    await flushPendingMessages(opencodeSessionId, mapping)

    if (mapping.pendingCleanup) {
      sessionMap.delete(opencodeSessionId)
      sessionMessageBuffer.delete(opencodeSessionId)
      await saveSessionMap()
    }
  }

  function resumeBackgroundCommits() {
    for (const [opencodeSessionId, mapping] of sessionMap.entries()) {
      if (mapping.commitInFlight) monitorBackgroundCommit(mapping, opencodeSessionId)
    }
  }

  function monitorBackgroundCommit(mapping, opencodeSessionId) {
    if (!mapping.commitTaskId) return
    if (commitWatchers.has(mapping.commitTaskId)) return

    const taskId = mapping.commitTaskId
    const watcher = waitForCommitCompletion(mapping, opencodeSessionId)
      .then((task) => {
        if (!task) {
          log("WARN", "session", "Background commit is still pending after the wait timeout", {
            task_id: taskId,
            openviking_session: mapping.ovSessionId,
            opencode_session: opencodeSessionId,
          })
        }
      })
      .catch((error) => {
        log("ERROR", "session", "Background commit watcher failed", {
          task_id: taskId,
          openviking_session: mapping.ovSessionId,
          opencode_session: opencodeSessionId,
          error: error?.message,
        })
      })
      .finally(() => {
        commitWatchers.delete(taskId)
      })
    commitWatchers.set(taskId, watcher)
  }

  async function flushAll({ commit = false } = {}) {
    if (saveTimer) {
      clearTimeout(saveTimer)
      saveTimer = null
    }
    for (const [sessionId, mapping] of sessionMap.entries()) {
      await flushPendingMessages(sessionId, mapping)
      if (commit) {
        if (mapping.commitInFlight) {
          monitorBackgroundCommit(mapping, sessionId)
        } else if (mapping.capturedMessages.size > 0) {
          await startBackgroundCommit(mapping, sessionId)
        }
      }
    }
    await saveSessionMap()
  }

  async function commitSession(sessionId, opencodeSessionId, abortSignal) {
    let mapping = opencodeSessionId ? sessionMap.get(opencodeSessionId) : undefined
    if (!mapping || mapping.ovSessionId !== sessionId) {
      mapping = createSessionMapping(sessionId)
    } else {
      await flushPendingMessages(opencodeSessionId, mapping)
    }

    if (mapping.commitInFlight) {
      const task = await waitForCommitCompletion(mapping, opencodeSessionId ?? sessionId, abortSignal)
      if (task?.status === "completed") return { status: "completed", task }
    }

    const start = await startBackgroundCommit(mapping, opencodeSessionId ?? sessionId, abortSignal)
    if (!start) throw new Error("Failed to start OpenViking session commit")
    if (start.mode === "completed") return { status: "completed", result: start.result }

    const task = await waitForCommitCompletion(mapping, opencodeSessionId ?? sessionId, abortSignal)
    if (!task) return { status: "accepted", task_id: start.taskId }
    return { status: task.status, task }
  }

  return {
    init,
    handleEvent,
    getMappedSessionId,
    commitSession,
    flushAll,
  }

  function createSessionMapping(ovSessionId) {
    return {
      ovSessionId,
      createdAt: Date.now(),
      capturedMessages: new Set(),
      messageRoles: new Map(),
      pendingMessages: new Map(),
      sendingMessages: new Set(),
      lastCommitTime: undefined,
      commitInFlight: false,
    }
  }

  function resolveEventSessionId(event) {
    return event?.properties?.info?.id ?? event?.properties?.sessionID ?? event?.properties?.sessionId
  }

  function mergeMessageContent(existing, incoming) {
    const next = incoming?.trim()
    if (!next) return existing ?? ""
    if (!existing) return next
    if (next === existing) return existing
    if (next.startsWith(existing)) return next
    if (existing.startsWith(next)) return existing
    if (next.includes(existing)) return next
    if (existing.includes(next)) return existing
    return `${existing}\n${next}`.trim()
  }

  function upsertBufferedMessage(sessionId, messageId, updates) {
    const now = Date.now()
    if (now - lastBufferCleanupAt >= BUFFER_CLEANUP_INTERVAL_MS) {
      cleanupOrphanedMessageBuffers(now)
      lastBufferCleanupAt = now
    }

    const freshBuffer = (sessionMessageBuffer.get(sessionId) ?? [])
      .filter((message) => now - message.timestamp <= BUFFERED_MESSAGE_TTL_MS)
    let buffered = freshBuffer.find((message) => message.messageId === messageId)
    if (!buffered) {
      while (freshBuffer.length >= MAX_BUFFERED_MESSAGES_PER_SESSION) freshBuffer.shift()
      buffered = { messageId, timestamp: now }
      freshBuffer.push(buffered)
    } else {
      buffered.timestamp = now
    }
    if (updates.role) buffered.role = updates.role
    if (updates.content) buffered.content = mergeMessageContent(buffered.content, updates.content)
    sessionMessageBuffer.set(sessionId, freshBuffer)
  }

  function cleanupOrphanedMessageBuffers(now) {
    for (const [sessionId, buffer] of sessionMessageBuffer.entries()) {
      if (sessionMap.has(sessionId)) continue
      const oldest = buffer[0]
      if (!oldest || now - oldest.timestamp > BUFFERED_MESSAGE_TTL_MS * 2) {
        sessionMessageBuffer.delete(sessionId)
      }
    }
  }

  function clearCommitState(mapping) {
    mapping.commitInFlight = false
    mapping.commitTaskId = undefined
    mapping.commitStartedAt = undefined
  }

  async function sleep(ms, abortSignal) {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, ms)
      if (!abortSignal) return
      const onAbort = () => {
        clearTimeout(timer)
        reject(new Error("Operation aborted"))
      }
      abortSignal.addEventListener("abort", onAbort, { once: true })
    })
  }
}
