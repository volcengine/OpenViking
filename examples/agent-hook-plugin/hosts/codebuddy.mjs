import { addAgentMessages, commitAgentSession } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { preToolUseOutput } from "../../memory-plugin-shared/lib/uri-guard.mjs";
import {
  applyIncrementalCaptureResult,
  buildIncrementalCapturePlan,
} from "./incremental-turn-capture.mjs";
import { buildCodebuddyTurns, cleanCodebuddyText } from "./codebuddy-turns.mjs";

// Keyword-mode gate. Language-neutral on purpose: a batch is capture-worthy
// when some user turn reads like a durable preference/fact rather than a task.
// The shared per-turn filter has no notion of capture modes, so the veto lives
// here; a vetoed batch stays unacknowledged and is re-judged on the next event.
const MEMORY_TRIGGERS = [
  /remember|preference|prefer|important|decision|decided|always|never/i,
  /记住|偏好|喜欢|喜爱|崇拜|讨厌|害怕|重要|决定|总是|永远|优先|习惯|爱好|擅长|最爱|不喜欢/i,
  /[\w.-]+@[\w.-]+\.\w+/,
  /\+\d{10,}/,
  /(?:我|my)\s*(?:是|叫|名字|name|住在|live|来自|from|生日|birthday|电话|phone|邮箱|email)/i,
  /(?:我|i)\s*(?:喜欢|崇拜|讨厌|害怕|擅长|不会|爱|恨|想要|需要|希望|觉得|认为|相信)/i,
  /(?:favorite|favourite|love|hate|enjoy|dislike|admire|idol|fan of)/i,
];

function hasMemoryTrigger(items) {
  return items.some((item) => item.turn.role === "user"
    && MEMORY_TRIGGERS.some((re) => re.test(item.content)));
}

export function shouldCommitCodebuddyCapture(event, cfg = {}, previous = 0, captured = 0) {
  if (captured <= 0) return false;
  if (event === "session-end") return true;
  if (event === "pre-compact") return cfg.autoCommitOnCompact !== false;
  return Number(previous || 0) + captured >= Number(cfg.commitTurnThreshold || Infinity);
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
    if (plan.toSend.length === 0) return null;

    if (ctx.cfg.captureMode === "keyword" && !hasMemoryTrigger(plan.toSend)) {
      ctx.log("skip", { reason: "keyword_mode_no_trigger", turns: plan.toSend.length });
      return null;
    }

    const result = await addAgentMessages(ctx.fetchJSON, ctx.sessionId, plan.payloads, ctx.peerId);
    const next = applyIncrementalCaptureResult(state, plan, result, cleanCodebuddyText);
    let capturedSinceCommit = Number(state.capturedSinceCommit || 0) + next.captured;
    if (shouldCommitCodebuddyCapture(event, ctx.cfg, state.capturedSinceCommit, next.captured)) {
      const committed = await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
      if (committed.ok) capturedSinceCommit = 0;
    }
    const { captured, ...persisted } = next;
    return { ...persisted, capturedSinceCommit };
  },
};
