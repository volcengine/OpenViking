import { evaluateUriGuard } from "../../memory-plugin-shared/lib/uri-guard.mjs";
import { captureIncrementalTurns } from "./incremental-turn-capture.mjs";
import { buildKimicodeTurns, cleanKimicodeText } from "./kimicode-turns.mjs";

function promptParts(input) {
  if (typeof input === "string") return input;
  if (!Array.isArray(input)) return "";
  return input.map((part) => (typeof part === "string" ? part : part?.text || "")).filter(Boolean).join("\n");
}

export const kimicode = {
  prefix: "kc-",
  tracksPendingPrompt: true,
  capturesOnlyWhenEnabled: true,
  detachesCapture: true,
  profileStage: "first-prompt",
  // Kimi drops a hook entry that carries any key beyond event, matcher,
  // command and timeout, so the replay bound lives here, not in the manifest.
  pendingReplayLimit: 2,
  requestBudgets: {
    "session-start": 25_000,
    "user-prompt-submit": 17_000,
    stop: 25_000,
    "pre-compact": 25_000,
    "session-end": 25_000,
    interrupt: 2_000,
  },
  stages: {
    "session-start": "start",
    "user-prompt-submit": "prompt",
    stop: "capture",
    "pre-compact": "capture",
    "session-end": "capture",
    interrupt: "capture-sync",
  },
  envelope(event, block) {
    return event === "user-prompt-submit" ? block || null : null;
  },
  // Only UserPromptSubmit output reaches the model, and the MCP server cannot
  // tell which session calls it.
  historyPointer: {
    sessionDefault: false,
    defer: (event, input) => {
      if (event === "pre-compact") return "compact";
      return event === "session-start" && input.source === "resume" ? "resume" : "";
    },
  },
  guard(input = {}) {
    const decision = evaluateUriGuard(input.tool_name, input.tool_input || {}, {
      guarded: new Set(["read", "glob", "grep"]),
    });
    if (!decision) return {};
    return {
      hookSpecificOutput: {
        permissionDecision: "deny",
        permissionDecisionReason: decision.reason,
      },
    };
  },
  prompt(input) {
    return cleanKimicodeText(promptParts(input.prompt));
  },
  async capture(ctx, state, event) {
    return captureIncrementalTurns(ctx, state, event, buildKimicodeTurns(ctx.input, state), {
      cleanText: cleanKimicodeText,
      endEvents: ["session-end", "interrupt"],
    });
  },
};
