import { denyHookSpecificOutput, noticeHookSpecificOutput, readToolEvent } from "../../lib/uri-guard.mjs";
import { decode } from "./decode.mjs";

const INJECTS = { "session-start": "SessionStart", "user-prompt-submit": "UserPromptSubmit" };

function promptText(raw) {
  if (typeof raw === "string") return raw.trim();
  if (!Array.isArray(raw)) return "";
  return raw
    .map((part) => (typeof part === "string" ? part : part?.text || ""))
    .filter(Boolean)
    .join("\n")
    .trim();
}

/** @type {import("../../lib/flow/host.d.mts").Host} */
export default {
  id: "codebuddy",
  sessionPrefix: "cb-",
  events: {
    "session-start": "start",
    "user-prompt-submit": "prompt",
    stop: "turnEnd",
    "pre-compact": "compactSoon",
    // Not fired by headless `-p` (DESIGN.md §3).
    "session-end": "end",
  },
  budgets: {
    "session-start": 25_000,
    "user-prompt-submit": 17_000,
    stop: 25_000,
    "pre-compact": 25_000,
    "session-end": 25_000,
  },
  // The host kills a timed-out hook but not its descendants (DESIGN.md §7).
  canDetach: true,
  // SessionStart context is dropped after a compaction and a --resume, so the
  // profile and the note wait for the next UserPromptSubmit (DESIGN.md §12).
  facts: { compactionDropsProfile: true },
  read(event, payload) {
    const { source } = payload;
    const resumed = event === "session-start" && (source === "compact" || source === "resume") ? source : "";
    return {
      // Host-generated continuations are not user asks; the flag was measured
      // on the wire, not documented (DESIGN.md §4).
      prompt: payload.is_internal_continuation === true ? "" : promptText(payload.prompt),
      eventId: event === "user-prompt-submit" ? String(payload.generation_id || "") : undefined,
      transcript: String(payload.transcript_path || payload.transcriptPath || "").trim(),
      resumed,
      defer: Boolean(resumed),
    };
  },
  decode,
  // The hooks.json matcher leaves the shell tools out: PreToolUse has no
  // advisory channel on this host, so only a deny reaches the model (DESIGN.md §5).
  tool(payload) {
    const { toolName, toolInput } = readToolEvent(payload);
    return { name: toolName, input: toolInput };
  },
  emit(event, output) {
    if (output.deny) return denyHookSpecificOutput(output.deny);
    if (output.notice) return noticeHookSpecificOutput(output.notice);
    const hookEventName = INJECTS[event];
    return hookEventName && output.context
      ? { decision: "approve", hookSpecificOutput: { hookEventName, additionalContext: output.context } }
      : null;
  },
  // Every MCP call's `_meta` names the session in W3C baggage; inside a
  // subagent the baggage keeps the parent's id, the session the hooks capture,
  // while `workbuddy.ai/conversationId` names the subagent's own (DESIGN.md §12).
  mcpSession(meta) {
    for (const entry of String(meta.baggage || "").split(",")) {
      const [key, ...value] = entry.split("=");
      if (key.trim() !== "codebuddy.session_id") continue;
      try {
        return decodeURIComponent(value.join("=").split(";")[0].trim());
      } catch {
        return "";
      }
    }
    const id = meta["workbuddy.ai/conversationId"];
    return typeof id === "string" ? id.trim() : "";
  },
};
