export function normalizeV2LifecycleEvent(event) {
  const type = event?.type
  const data = event?.data && typeof event.data === "object" ? event.data : {}
  const sessionID = data.sessionID ?? data.sessionId
  if (!type || !sessionID) return []

  if (type === "session.created") {
    return [sessionEvent("session.created", sessionID, { parentID: data.parentID })]
  }
  if (type === "session.deleted") {
    return [sessionEvent("session.deleted", sessionID)]
  }
  if (type === "session.compaction.ended") {
    return [sessionEvent("session.compacted", sessionID)]
  }
  // A failed execution ends one turn, not the session, so it keeps state like
  // the other terminal outcomes instead of taking v1's session.error path.
  if (
    type === "session.execution.succeeded" ||
    type === "session.execution.interrupted" ||
    type === "session.execution.failed"
  ) {
    return [sessionEvent("session.idle", sessionID)]
  }
  return []
}

export function contextMessageEvents(sessionID, message) {
  if (!sessionID || !message?.id) return []
  if (message.type === "user") {
    if (typeof message.text !== "string" || !message.text.trim()) return []
    return [
      messageUpdated(sessionID, message.id, "user"),
      partUpdated(sessionID, message.id, `${message.id}:text`, { type: "text", text: message.text }),
    ]
  }
  if (message.type !== "assistant") return []

  const events = [messageUpdated(sessionID, message.id, "assistant")]
  for (const [index, block] of (message.content ?? []).entries()) {
    const part = contentPart(block, index)
    if (part) events.push(partUpdated(sessionID, message.id, part.id, part))
  }
  return events.length > 1 ? events : []
}

function sessionEvent(type, sessionID, info = {}) {
  return {
    type,
    properties: { info: { id: sessionID, sessionID, ...info } },
  }
}

function contentPart(block, index) {
  if (!block || typeof block !== "object") return null
  // Reasoning is dropped, matching v1 parts and the shared capture filter.
  if (block.type === "text") {
    return {
      id: `text:${index}`,
      type: "text",
      text: typeof block.text === "string" ? block.text : "",
    }
  }
  if (block.type !== "tool") return null
  const output = toolContentText(block.state?.content) || errorText(block.state?.error)
  return {
    id: block.id || `tool:${index}`,
    type: "tool",
    callID: block.id,
    tool: block.name,
    state: block.state,
    output,
  }
}

function toolContentText(content) {
  if (!Array.isArray(content)) return ""
  return content
    .filter((item) => item?.type === "text" && typeof item.text === "string")
    .map((item) => item.text)
    .join("\n\n")
}

function errorText(error) {
  if (typeof error === "string") return error
  if (typeof error?.message === "string") return error.message
  return ""
}

function messageUpdated(sessionID, messageID, role) {
  return {
    type: "message.updated",
    properties: { info: { sessionID, id: messageID, role } },
  }
}

function partUpdated(sessionID, messageID, partId, part) {
  return {
    type: "message.part.updated",
    properties: { part: { ...part, id: partId, sessionID, messageID } },
  }
}
