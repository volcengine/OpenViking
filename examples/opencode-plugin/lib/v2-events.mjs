export function normalizeV2Event(event) {
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
  if (type === "session.compacted") {
    return [sessionEvent("session.compacted", sessionID)]
  }
  if (type === "session.execution.succeeded" || type === "session.execution.interrupted") {
    return [sessionEvent("session.idle", sessionID)]
  }
  if (type === "session.execution.failed") {
    return [{
      ...sessionEvent("session.error", sessionID),
      error: data.error ?? data.message,
    }]
  }
  if (type === "session.message.content.updated") {
    return messageContentEvents(data)
  }
  return []
}

export function userPromptEvents({ sessionID, messageID, text }) {
  if (!sessionID || !messageID || typeof text !== "string" || !text.trim()) return []
  return [
    messageUpdated(sessionID, messageID, "user"),
    partUpdated(sessionID, messageID, "prompt", { type: "text", text }),
  ]
}

function sessionEvent(type, sessionID, info = {}) {
  return {
    type,
    properties: {
      info: {
        id: sessionID,
        sessionID,
        ...info,
      },
    },
  }
}

function messageContentEvents(data) {
  const sessionID = data.sessionID ?? data.sessionId
  const messageID = data.messageID ?? data.messageId
  if (!sessionID || !messageID) return []
  const events = [messageUpdated(sessionID, messageID, "assistant")]
  const content = Array.isArray(data.content) ? data.content : []
  content.forEach((block, index) => {
    const part = contentPart(sessionID, messageID, block, index)
    if (part) events.push(partUpdated(sessionID, messageID, part.id, part))
  })
  return events
}

function contentPart(sessionID, messageID, block, index) {
  if (!block || typeof block !== "object") return null
  if (block.type === "text" || block.type === "reasoning") {
    return {
      id: `${block.type}:${index}`,
      sessionID,
      messageID,
      type: "text",
      text: typeof block.text === "string" ? block.text : "",
    }
  }
  if (block.type !== "tool") return null
  const content = block.state?.content
  const text = toolContentText(content) || block.state?.error?.message || ""
  return {
    id: block.id || `tool:${index}`,
    sessionID,
    messageID,
    type: "tool",
    name: block.name,
    tool_use_id: block.id,
    content,
    text,
    output: text,
  }
}

function toolContentText(content) {
  if (!Array.isArray(content)) return ""
  return content
    .filter((item) => item?.type === "text" && typeof item.text === "string")
    .map((item) => item.text)
    .join("\n\n")
}

function messageUpdated(sessionID, messageID, role) {
  return {
    type: "message.updated",
    properties: {
      info: { sessionID, id: messageID, role },
    },
  }
}

function partUpdated(sessionID, messageID, partId, part) {
  return {
    type: "message.part.updated",
    properties: {
      part: { ...part, id: partId, sessionID, messageID },
    },
  }
}
