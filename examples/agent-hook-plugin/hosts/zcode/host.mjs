/**
 * ZCode validates hook stdout against a strict JSON schema and silently
 * discards the whole document when it meets a key it does not know, so an
 * envelope never carries a Claude-Code-ism such as `{ "decision": "approve" }`
 * and a pass-through prints nothing at all.
 */

import { join } from "node:path";

import { sanitizeCapturedText } from "../../lib/capture-utils.mjs";
import { denyHookSpecificOutput, noticeHookSpecificOutput, readToolEvent } from "../../lib/uri-guard.mjs";
import { decode } from "./decode.mjs";

const INJECTS = { "session-start": "SessionStart", "user-prompt-submit": "UserPromptSubmit" };

/** @type {import("../../lib/flow/host.d.mts").Host} */
export default {
  id: "zcode",
  sessionPrefix: "zc-",
  // No session-end or compaction event: commits are the server's auto-commit.
  events: {
    "session-start": "start",
    "user-prompt-submit": "prompt",
    stop: "turnEnd",
  },
  budgets: { "session-start": 25_000, "user-prompt-submit": 17_000, stop: 25_000 },
  canDetach: true,
  // The rollout file is found by session id under HOME and may not be there.
  facts: { transcript: "optional" },
  read(event, payload) {
    const raw = payload.prompt || payload.user_prompt || payload.userMessage || payload.user_message
      || payload.message || "";
    const prompt = sanitizeCapturedText(typeof raw === "string" ? raw : "");
    return {
      prompt,
      ...(event === "user-prompt-submit" && prompt ? { note: { prompt } } : {}),
      // The Stop payload's transcript_path is a temp file with only the last
      // reply; the rollout file named by the session id holds the conversation.
      transcript: String(payload.session_id || payload.sessionId || payload.conversation_id || "").trim(),
    };
  },
  locate(sessionId) {
    const home = process.env.HOME || process.env.USERPROFILE || "";
    return sessionId && home ? join(home, ".zcode", "cli", "rollout", `model-io-${sessionId}.jsonl`) : null;
  },
  decode,
  tool(payload) {
    const { toolName, toolInput } = readToolEvent(payload);
    return { name: toolName, input: toolInput };
  },
  emit(event, output) {
    if (output.deny) return denyHookSpecificOutput(output.deny);
    // The PreToolUse matcher names no shell tool, so a notice never fires:
    // ZCode's strict schema is not verified to accept additionalContext there.
    if (output.notice) return noticeHookSpecificOutput(output.notice);
    const hookEventName = INJECTS[event];
    return hookEventName && output.context
      ? { hookSpecificOutput: { hookEventName, additionalContext: output.context } }
      : null;
  },
};
