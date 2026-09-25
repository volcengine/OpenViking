import { createUserMessage } from "@deepseek-ai/dsh-llm";
import {
  extractTextFromPayload,
  shapeCapturePayload,
} from "./shared/capture-utils.mjs";

export const OPENVIKING_PLUGIN_SOURCE = "openviking-memory";
export const OPENVIKING_PLUGIN_KIND = `plugin:${OPENVIKING_PLUGIN_SOURCE}`;

export function pluginMessage(content, source) {
  // dsh's own constructor: identity, normalization, and any future Message
  // invariants come from the pinned peer instead of a hand-built object.
  return createUserMessage({
    content: [{ type: "text", text: content }],
    source: {
      kind: OPENVIKING_PLUGIN_KIND,
      plugin: OPENVIKING_PLUGIN_SOURCE,
      ...source,
    },
  });
}

export function isOpenVikingPluginMessage(message) {
  const source = message?.source;
  return source?.kind === OPENVIKING_PLUGIN_KIND
    || (source?.kind === "plugin" && source.plugin === OPENVIKING_PLUGIN_SOURCE);
}

function isSyntheticUserMessage(message) {
  const kind = message?.source?.kind;
  if (kind === "plugin") return true;
  if (typeof kind === "string" && kind.startsWith("plugin:")) return true;
  // DSH's v4 migration gives first-party context producers their own kinds
  // (for example, "time-context") instead of the retired plugin wrapper.
  return message?.role === "user"
    && typeof kind === "string"
    && kind !== "user"
    && kind !== "tool";
}

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
  // DSH v4 gives producer-owned context messages their own kind. Treat
  // user-role messages with an explicit non-user/tool kind as synthetic;
  // untagged legacy messages stay capturable for backward compatibility.
  if (isSyntheticUserMessage(message)) return null;
  if (message.role === "assistant" && config.captureAssistantTurns === false) {
    return null;
  }
  if (message.source?.kind === "tool" && config.captureToolResults !== true) {
    return null;
  }

  const role = message.role === "assistant" ? "assistant" : "user";
  const toolNameById = Object.fromEntries(toolNames);
  const shaped = shapeCapturePayload(message, role, config, {
    toolNameById,
  });
  if (shaped.dropped) return null;
  const structuredParts = shaped.parts.filter(part => part?.type !== "text");
  if (!shaped.text && structuredParts.length === 0) return null;

  const hasTextPart = shaped.parts.some(part => part?.type === "text");
  const bodyParts = [
    ...(hasTextPart && shaped.text
      ? [{ type: "text", text: shaped.text }]
      : []),
    ...structuredParts,
  ];
  const payload = bodyParts.length > 0
    ? { role, parts: bodyParts }
    : { role, content: shaped.text };
  const createdAt = eventCreatedAt(event);
  if (createdAt) payload.created_at = createdAt;
  if (config.peerId) payload.peer_id = config.peerId;
  return payload;
}

export function promptText(messages) {
  return (messages || [])
    .filter(message => !isOpenVikingPluginMessage(message))
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
