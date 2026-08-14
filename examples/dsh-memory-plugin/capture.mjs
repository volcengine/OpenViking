import {
  extractPartsFromPayload,
  extractTextFromPayload,
  shouldCaptureText,
} from "./shared/capture-utils.mjs";

export const OPENVIKING_PLUGIN_SOURCE = "openviking-memory";

export function captureEvent(event, config, toolNames = new Map()) {
  if (!event || typeof event !== "object") return null;
  if (event.type === "tool/call") {
    if (config.captureToolResults === true) {
      toolNames.set(String(event.data.callId), event.data.name);
    }
    return null;
  }

  const message = eventMessage(event);
  if (!message) return null;
  const toolCallId = event.type === "tool/result"
    ? String(message.source?.callId || message.content?.[0]?.toolCallId || "")
    : "";
  try {
    return captureMessage(event, message, config, toolNames);
  } finally {
    if (toolCallId) toolNames.delete(toolCallId);
  }
}

function captureMessage(event, message, config, toolNames) {
  // Whitelist by source: plugin-injected user messages (this plugin's recall
  // blocks, time-context snapshots, any other plugin's context) are model
  // input, not human input — mirroring them would launder synthetic text
  // into memory as if a person said it.
  if (message.source?.kind === "plugin") return null;
  if (message.role === "assistant" && config.captureAssistantTurns === false) {
    return null;
  }
  if (message.source?.kind === "tool" && config.captureToolResults !== true) {
    return null;
  }

  const role = message.role === "assistant" ? "assistant" : "user";
  const toolNameById = Object.fromEntries(toolNames);
  const rawText = extractTextFromPayload(message, {
    toolMaxChars: config.captureToolMaxChars,
  });
  const parts = extractPartsFromPayload(message, {
    toolMaxChars: config.captureToolMaxChars,
    toolNameById,
  });
  const decision = shouldCaptureText(rawText, role, config);
  const structuredParts = parts.filter(part => part?.type !== "text");
  if (!decision.shouldCapture && structuredParts.length === 0) return null;

  const hasTextPart = parts.some(part => part?.type === "text");
  const bodyParts = [
    ...(hasTextPart && decision.shouldCapture && decision.text
      ? [{ type: "text", text: decision.text }]
      : []),
    ...structuredParts,
  ];
  const payload = bodyParts.length > 0
    ? { role, parts: bodyParts }
    : { role, content: decision.text };
  const createdAt = eventCreatedAt(event);
  if (createdAt) payload.created_at = createdAt;
  if (config.peerId) payload.peer_id = config.peerId;
  return payload;
}

export function promptText(messages) {
  return (messages || [])
    .filter(message => !(
      message?.source?.kind === "plugin"
      && message.source.plugin === OPENVIKING_PLUGIN_SOURCE
    ))
    .map(message => extractTextFromPayload(message))
    .filter(Boolean)
    .join("\n\n")
    .trim();
}

function eventMessage(event) {
  switch (event.type) {
    case "user/message":
      return event.data;
    case "assistant/message":
    case "tool/result":
      return event.data?.message;
    default:
      return null;
  }
}

function eventCreatedAt(event) {
  const time = Number(event?.time);
  if (!Number.isFinite(time) || time < 0) return "";
  try {
    return new Date(time).toISOString();
  } catch {
    return "";
  }
}
