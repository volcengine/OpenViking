import { OPENCODE_MCP_NAME } from "./mcp-config.mjs"
import { normalizeToolName } from "./shared/uri-guard.mjs"
import { log } from "./utils.mjs"

const SESSION_HISTORY_TOOL = `${OPENCODE_MCP_NAME}_session_history`

// OpenCode's MCP server is shared by every session it serves, so the calling
// session is only known here. A session the model chose is kept; only a
// missing or blank one is filled. The host reads `args` back by reference.
export function fillHistorySession(sessionManager, { tool, sessionID, args }) {
  if (normalizeToolName(tool) !== SESSION_HISTORY_TOOL || !sessionID) return
  if (!args || typeof args !== "object") return
  const given = args.session
  if (given != null && (typeof given !== "string" || given.trim())) return
  args.session = sessionManager.getMappedSessionId(sessionID)
  log("INFO", "session-history", "Filled session_history session", {
    opencode_session: sessionID,
    openviking_session: args.session,
  })
}
