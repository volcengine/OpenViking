import { existsSync, readFileSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { cleanKimicodeText, decode } from "./decode.mjs";

const GUARDED = new Set(["read", "glob", "grep"]);

const kimiCodeHome = () => process.env.KIMI_CODE_HOME || join(homedir(), ".kimi-code");

function wirePath(sessionId, home = kimiCodeHome()) {
  const indexPath = join(home, "session_index.jsonl");
  if (existsSync(indexPath)) {
    try {
      for (const line of readFileSync(indexPath, "utf8").split("\n")) {
        if (!line.trim()) continue;
        const row = JSON.parse(line);
        if (row.sessionId === sessionId && row.sessionDir) return join(row.sessionDir, "agents", "main", "wire.jsonl");
      }
    } catch {
      // Fall back to the session directory scan.
    }
  }
  const sessionsRoot = join(home, "sessions");
  try {
    for (const cwdKey of readdirSync(sessionsRoot)) {
      const candidate = join(sessionsRoot, cwdKey, sessionId, "agents", "main", "wire.jsonl");
      if (existsSync(candidate)) return candidate;
    }
  } catch {
    return null;
  }
  return null;
}

/** @type {import("../../lib/flow/host.d.mts").Host} */
export default {
  id: "kimicode",
  sessionPrefix: "kc-",
  events: {
    "session-start": "start",
    "user-prompt-submit": "prompt",
    stop: "turnEnd",
    "pre-compact": "compactSoon",
    "session-end": "end",
    // Kimi fires Interrupt, not Stop, after a cancelled turn.
    interrupt: "end",
  },
  budgets: {
    "session-start": 25_000,
    "user-prompt-submit": 17_000,
    stop: 25_000,
    "pre-compact": 25_000,
    "session-end": 25_000,
    interrupt: 2_000,
  },
  canDetach: true,
  syncEvents: ["interrupt"],
  facts: {
    // Only UserPromptSubmit output reaches the model.
    profileAt: "firstPrompt",
    // The MCP server cannot tell which session calls it.
    historySession: "explicit",
    // Kimi drops a hook entry that carries any key beyond event, matcher,
    // command and timeout, so the replay bound lives here, not in the manifest.
    replayLimit: 2,
  },
  read(event, payload) {
    const sessionId = String(payload.session_id || "").trim();
    return {
      // `prompt` may be a string or an array of content parts.
      prompt: cleanKimicodeText(payload.prompt),
      transcript: sessionId,
      resumed: event === "session-start" && payload.source === "resume" ? "resume" : "",
    };
  },
  locate: (sessionId) => (sessionId ? wirePath(String(sessionId)) : null),
  decode,
  tool(payload) {
    const name = String(payload.tool_name || "");
    return GUARDED.has(name.toLowerCase()) ? { name, input: payload.tool_input || {} } : null;
  },
  emit(event, output) {
    if (output.deny) {
      return { hookSpecificOutput: { permissionDecision: "deny", permissionDecisionReason: output.deny } };
    }
    // UserPromptSubmit takes raw text on stdout.
    return event === "user-prompt-submit" && output.context ? output.context : null;
  },
};
