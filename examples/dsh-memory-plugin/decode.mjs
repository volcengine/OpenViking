import { extractTextFromPayload } from "./shared/capture-utils.mjs";

export const OPENVIKING_PLUGIN_SOURCE = "openviking-memory";
export const OPENVIKING_PLUGIN_KIND = `plugin:${OPENVIKING_PLUGIN_SOURCE}`;

// Tool names kept for results that arrive after their call.
const KEEP_TOOL_NAMES = 200;

export function isSyntheticUserMessage(message) {
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

// Recall queries use only what the user typed. Injected context (time-context,
// job notices, other plugins' blocks, our own recall) and tool results would
// otherwise trigger recall on their own and pollute the query text.
function isUserInput(message) {
  if (!message || message.role !== "user") return false;
  if (message.source?.kind === "tool") return false;
  return !isSyntheticUserMessage(message);
}

export function promptText(messages) {
  return (messages || [])
    .filter(isUserInput)
    .map(message => extractTextFromPayload(message))
    .filter(Boolean)
    .join("\n\n")
    .trim();
}

function eventMessage(event) {
  if (event?.type === "user/message") return event.data;
  if (event?.type === "assistant/message" || event?.type === "tool/result") return event.data?.message;
  return null;
}

/**
 * DSH pushes one session event at a time and keeps no transcript a plugin can
 * read, so a capture's turn is the event in the payload. The cursor only holds
 * the tool names a later result needs.
 *
 * @param {import("./shared/flow/host.d.mts").DecodeInput} input
 */
export function decode({ cursor, payload = {}, cfg = {} }) {
  const known = cursor && typeof cursor.toolNames === "object" && cursor.toolNames ? cursor.toolNames : {};
  const toolNames = { ...known };
  const event = payload.event;
  const done = turns => ({
    turns,
    toolNames: known,
    cursorAfter: () => ({ toolNames: Object.fromEntries(Object.entries(toolNames).slice(-KEEP_TOOL_NAMES)) }),
  });
  const tools = cfg.captureToolResults === true;

  if (event?.type === "tool/call") {
    if (!tools) return done([]);
    toolNames[String(event.data?.callId)] = event.data?.name;
    // Nothing to send; the turn only settles the cursor that keeps the name.
    return done([{ role: "assistant", content: "" }]);
  }
  const message = eventMessage(event);
  if (!message || isSyntheticUserMessage(message)) return done([]);
  if (message.source?.kind === "tool" && !tools) return done([]);
  if (event.type === "tool/result") {
    delete toolNames[String(message.source?.callId || message.content?.[0]?.toolCallId || "")];
  }
  const role = message.role === "assistant" ? "assistant" : "user";
  // The event's own time: a write replayed from the offline queue must not
  // land on the day of the replay.
  const time = Number(event.time);
  return done([{
    role,
    content: extractTextFromPayload(message),
    payload: { role, content: message.content },
    ...(Number.isFinite(time) && time >= 0 ? { createdAt: new Date(time).toISOString() } : {}),
  }]);
}
