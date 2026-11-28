/**
 * OpenCode 2 runs the plugin inside its own process and hands it no
 * transcript file: the wiring in lib/v2-plugin.mjs puts the session's messages
 * (`ctx.session.context()`) into the payload, and `decode` reads them. A sent
 * message is remembered by its OpenCode id, so a message that comes back after
 * a compaction is not sent twice.
 */

import { readFileSync } from "node:fs"
import { join } from "node:path"

import { normalizeToolName } from "./lib/shared/uri-guard.mjs"

const SESSION_HISTORY_TOOL = "openviking_session_history"

const TOOL_HINTS = {
  read: { tool: "openviking_read", example: (uri) => `openviking_read(uris=["${uri}"])` },
  glob: { tool: "openviking_glob", example: (uri) => `openviking_glob(uri="${uri}", pattern="**/*")` },
  grep: {
    tool: "openviking_search",
    example: (uri, args = {}) => `openviking_search(query="${String(args.pattern ?? "").replaceAll('"', '\\"')}", target_uri="${uri}")`,
  },
  bash: { tool: "openviking_read or openviking_search", example: (uri) => `openviking_read(uris=["${uri}"])` },
  shell: { tool: "openviking_read or openviking_search", example: (uri) => `openviking_read(uris=["${uri}"])` },
}

const textOf = (content) => (Array.isArray(content) ? content : [])
  .filter((item) => item?.type === "text" && typeof item.text === "string")
  .map((item) => item.text)
  .join("\n\n")

function errorText(error) {
  if (typeof error === "string") return error
  return typeof error?.message === "string" ? error.message : ""
}

/** One OpenCode 2 message as a turn, or null when it has nothing to send. Reasoning is dropped. */
function turnOf(message) {
  if (!message?.id) return null
  if (message.type === "user") {
    const text = typeof message.text === "string" ? message.text : ""
    return text.trim() ? { role: "user", content: text, turnId: message.id } : null
  }
  if (message.type !== "assistant") return null
  const content = []
  for (const block of message.content ?? []) {
    if (block?.type === "text" && typeof block.text === "string") content.push({ type: "text", text: block.text })
    else if (block?.type === "tool") {
      content.push({
        type: "tool",
        id: block.id,
        name: block.name,
        state: { status: block.state?.status, input: block.state?.input, error: block.state?.error },
        output: textOf(block.state?.content) || errorText(block.state?.error),
      })
    }
  }
  if (content.length === 0) return null
  return { role: "assistant", content: textOf(content), turnId: message.id, payload: { role: "assistant", content } }
}

/** @param {import("./lib/shared/flow/host.d.mts").DecodeInput} input */
export function decode({ cursor = {}, payload = {} }) {
  const messages = Array.isArray(payload.messages) ? payload.messages : []
  const sent = new Set(Array.isArray(cursor.ids) ? cursor.ids : [])
  // The 0.5 plugin kept the id of the last message it captured in plugin storage.
  const after = cursor.after ? messages.findIndex((message) => message?.id === cursor.after) : -1
  for (const message of messages.slice(0, after + 1)) if (message?.id) sent.add(message.id)
  const pending = messages.filter((message) => message?.id && !sent.has(message.id)).map(turnOf).filter(Boolean)
  // Bounded by the context window: an id no longer in it cannot come back.
  const present = new Set(messages.map((message) => message?.id))
  const kept = [...sent].filter((id) => present.has(id))
  return {
    turns: pending,
    cursorAfter: (count) => ({ ids: [...kept, ...pending.slice(0, count).map((turn) => turn.turnId)] }),
  }
}

/** The ids the 0.5 plugin marked captured in its own state file, or [] when it has none. */
function legacyCapturedIds(dataDir, sessionID) {
  if (!dataDir || !sessionID) return []
  try {
    const state = JSON.parse(readFileSync(join(dataDir, "openviking-session-state.json"), "utf8"))
    return (state.sessions?.[sessionID]?.messages ?? [])
      .filter(([, message]) => message?.captured)
      .map(([id]) => id)
  } catch {
    return []
  }
}

/**
 * The OpenCode host. `dataDir` is where the 0.5 plugin kept its session state;
 * `mcpEnabled` says whether the bundled MCP server, which fills
 * session_history's `session`, is registered.
 *
 * @returns {import("./lib/shared/flow/host.d.mts").Host}
 */
export function createOpencodeHost({ dataDir = "", mcpEnabled = true } = {}) {
  return {
    id: "opencode",
    sessionPrefix: "oc-",
    events: {
      "session.created": "start",
      prompt: "prompt",
      "execution.ended": "turnEnd",
      compaction: "compactSoon",
      "session.deleted": "end",
      "execute.before": "toolCall",
    },
    facts: {
      // The prompt hook is the only place the plugin can add context.
      profileAt: "firstPrompt",
      compactionDropsProfile: true,
      // context() no longer returns the turns before a compaction once it ends.
      compactionDropsTranscript: true,
      transcript: false,
      historySession: mcpEnabled ? "default" : "explicit",
    },
    toolHints: TOOL_HINTS,
    read(event, payload) {
      const parent = payload.parentID || ""
      return {
        sessionId: parent || payload.sessionID,
        ...(parent ? { child: `subagent-${payload.sessionID}` } : {}),
        cwd: payload.directory || undefined,
        ...(event === "prompt" ? { prompt: String(payload.prompt || "").trim(), eventId: payload.messageID || undefined } : {}),
        legacy: { sessionID: payload.sessionID, after: payload.storedCursor },
      }
    },
    decode,
    legacyCursor(_nativeId, read) {
      const ids = legacyCapturedIds(dataDir, read.legacy?.sessionID)
      const after = read.legacy?.after
      if (ids.length === 0 && !after) return undefined
      return { ids, ...(after ? { after } : {}) }
    },
    tool(payload) {
      const name = normalizeToolName(payload.tool)
      if (!name) return null
      if (name === SESSION_HISTORY_TOOL) return { history: payload.input }
      return { name, input: payload.input && typeof payload.input === "object" ? payload.input : {} }
    },
    emit: (_event, output) => output,
  }
}

export default createOpencodeHost()
