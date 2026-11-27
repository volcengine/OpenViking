import { denyHookSpecificOutput, noticeHookSpecificOutput, readToolEvent } from "../../lib/uri-guard.mjs";
import { cleanTraeText, decode } from "./decode.mjs";

const INJECTS = {
  "session-start": "SessionStart",
  "user-prompt-submit": "UserPromptSubmit",
  "post-compact": "PostCompact",
};

/** @type {import("../../lib/flow/host.d.mts").Host} */
const trae = {
  id: "trae",
  sessionPrefix: "tr-",
  events: {
    "session-start": "start",
    "user-prompt-submit": "prompt",
    // TRAE holds PostCompact context for the next user prompt.
    "post-compact": "compacted",
    stop: "turnEnd",
  },
  budgets: { "session-start": 25_000, "user-prompt-submit": 17_000, "post-compact": 1_000, stop: 25_000 },
  facts: { transcript: false, historySession: "explicit" },
  read(event, payload) {
    if (event === "user-prompt-submit") {
      const prompt = cleanTraeText(payload.prompt);
      return {
        prompt,
        eventId: payload.generation_id || undefined,
        ...(prompt ? { note: { prompt } } : {}),
      };
    }
    return { resumed: event === "post-compact" ? "compact" : "" };
  },
  decode,
  tool(payload) {
    const { toolName, toolInput } = readToolEvent(payload);
    return { name: toolName, input: toolInput };
  },
  emit(event, output) {
    if (output.deny) return denyHookSpecificOutput(output.deny);
    if (output.notice) return noticeHookSpecificOutput(output.notice);
    if (!event) return null;
    const value = { decision: "approve" };
    if (output.context) {
      value.hookSpecificOutput = { hookEventName: INJECTS[event] || "UserPromptSubmit", additionalContext: output.context };
    }
    return value;
  },
  // `_meta.chatSessionId` is read from the shipped app, not seen on the wire,
  // so the session-history note still asks the model to pass `session`.
  mcpSession: (meta) => (typeof meta.chatSessionId === "string" ? meta.chatSessionId.trim() : ""),
};

// TRAE CN is the same host with its own configuration key and session prefix.
export const hosts = { trae, "trae-cn": { ...trae, id: "trae-cn", sessionPrefix: "trcn-" } };
export default trae;
