import { addAgentMessages, commitAgentSession } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { preToolUseOutput } from "../../memory-plugin-shared/lib/uri-guard.mjs";
import {
  applyIncrementalCaptureResult,
  buildIncrementalCapturePlan,
} from "./incremental-turn-capture.mjs";
import { buildCodebuddyTurns, cleanCodebuddyText } from "./codebuddy-turns.mjs";

export function shouldCommitCodebuddyCapture(event, cfg = {}, previous = 0, captured = 0) {
  const uncommitted = Number(previous || 0) + captured;
  if (uncommitted <= 0) return false;
  if (event === "session-end") return true;
  if (event === "pre-compact") return cfg.autoCommitOnCompact !== false;
  return uncommitted >= Number(cfg.commitTurnThreshold || Infinity);
}

export const codebuddy = {
  prefix: "cb-",
  tracksPendingPrompt: true,
  capturesOnlyWhenEnabled: true,
  detachesCapture: true,
  profileStage: "session-start",
  requestBudgets: {
    "session-start": 25_000,
    "user-prompt-submit": 17_000,
    stop: 25_000,
    "pre-compact": 25_000,
    "session-end": 25_000,
  },
  stages: {
    "session-start": "start",
    "user-prompt-submit": "prompt",
    stop: "capture",
    "pre-compact": "capture",
    "session-end": "capture",
  },
  envelope(event, block) {
    // CodeBuddy reads the Claude Code envelope on both injection events and
    // nothing on the write-path ones (DESIGN.md §5). An empty envelope is
    // verified safe, so a stage with nothing to say answers nothing.
    if (event === "session-start" && block) {
      return { decision: "approve", hookSpecificOutput: { hookEventName: "SessionStart", additionalContext: block } };
    }
    if (event === "user-prompt-submit" && block) {
      return { decision: "approve", hookSpecificOutput: { hookEventName: "UserPromptSubmit", additionalContext: block } };
    }
    return null;
  },
  guard(input = {}) {
    // Deny-only in effect: the hooks.json matcher leaves the shell tools out,
    // and CodeBuddy's PreToolUse has no advisory channel for a notice anyway
    // (DESIGN.md §5). The shared evaluator supplies the deny envelope.
    return preToolUseOutput(input);
  },
  prompt(input) {
    // Host-generated continuations are not user asks — never search for them.
    // The flag is not in the host docs; it was measured on the wire (DESIGN.md
    // §4).
    if (input.is_internal_continuation === true) return "";
    // The payload's `prompt` is the raw ask: host markup wraps transcript
    // records, not this field, so no stripping or sanitizing belongs here.
    const raw = input.prompt;
    if (typeof raw === "string") return raw.trim();
    if (Array.isArray(raw)) {
      return raw
        .map((part) => (typeof part === "string" ? part : part?.text || ""))
        .filter(Boolean)
        .join("\n")
        .trim();
    }
    return "";
  },
  async capture(ctx, state, event) {
    const transcript = buildCodebuddyTurns(ctx.input, state, ctx.cfg);
    if (transcript.status !== "ok") {
      ctx.log(`capture_${transcript.status}`, transcript.error ? { error: transcript.error } : {});
      return null;
    }
    const plan = buildIncrementalCapturePlan(transcript.turns, state, ctx.cfg);
    let next;
    if (plan.toSend.length > 0) {
      const result = await addAgentMessages(ctx.fetchJSON, ctx.sessionId, plan.payloads, ctx.peerId);
      next = applyIncrementalCaptureResult(state, plan, result, cleanCodebuddyText);
    } else if (shouldCommitCodebuddyCapture(event, ctx.cfg, state.capturedSinceCommit, 0)) {
      // Nothing new to send, but turns sent earlier are still uncommitted; a
      // session end or compaction may be their last chance.
      next = { ...state, captured: 0 };
    } else {
      return null;
    }
    let capturedSinceCommit = Number(state.capturedSinceCommit || 0) + next.captured;
    if (shouldCommitCodebuddyCapture(event, ctx.cfg, state.capturedSinceCommit, next.captured)) {
      const committed = await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
      if (committed.ok || committed.pendingQueued) capturedSinceCommit = 0;
    }
    const { captured, ...persisted } = next;
    return { ...persisted, capturedSinceCommit };
  },
};
