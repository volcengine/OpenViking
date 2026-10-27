/**
 * MiniMax Code adapter.
 *
 * Output contract: MiniMax Code accepts `hookSpecificOutput.additionalContext`
 * on the events that inject context, and it requires `hookEventName` to equal
 * the active event name exactly. A pass-through therefore writes nothing at
 * all, so a failed memory service never blocks the turn.
 *
 * The host's own event names are used as the stage keys, so the dispatcher can
 * forward them straight into the envelope without a translation table.
 *
 * Timing: MiniMax Code kills a hook at 10 seconds, and the shared recall path
 * keeps a 15-second floor on its context request. Every budget below stays
 * under the host's limit, and `recallContextTimeoutMs` in the `mcode` config
 * section lowers the request deadline to match.
 */

import { addAgentMessages, commitAgentSession } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { evaluateUriGuard, evaluateUriNotice, readToolEvent } from "../../memory-plugin-shared/lib/uri-guard.mjs";
import {
  applyIncrementalCaptureResult,
  buildIncrementalCapturePlan,
} from "./incremental-turn-capture.mjs";
import { buildMcodeTurns, cleanMcodeText } from "./mcode-turns.mjs";

/** A hook gets at most 10 seconds, so every request budget stays below it. */
const HOOK_BUDGET_MS = 9_000;
/**
 * The write path runs in a detached worker, so it is not bound by the host's
 * ten seconds and may take longer.
 *
 * This matters more than it looks: a capture that is cut short by a tight
 * request budget is queued locally instead, and a session start that then
 * replays a large queue spends its whole budget on the queue and delivers no
 * profile at all. The read path stays synchronous and short; the write path
 * gets room to finish.
 */
const WRITE_BUDGET_MS = 60_000;
/** SessionEnd handlers share a 3-second budget across the whole event. */
const SESSION_END_BUDGET_MS = 2_000;
/**
 * How much captured text one commit may carry.
 *
 * A commit hands its archive to the extraction model, and that model rejects
 * an input that exceeds its message-token limit. Sending a whole long
 * conversation in one commit therefore fails with "Total tokens of image and
 * text exceed max message tokens" and the turn is archived but never
 * extracted. Committing in slices of this size keeps every extraction inside
 * the limit, and the cursor makes the next capture continue where this one
 * stopped.
 */
const COMMIT_CHUNK_CHARS = 20_000;
/**
 * How many slices one capture may commit, and how long to wait between them.
 *
 * Every commit asks the server to archive and then extract, and the extraction
 * holds a lock on the session while it runs. Committing a large backlog in one
 * burst therefore trips "lock error: lock acquire timed out". Bounding the
 * burst and spacing the commits keeps the server's queue healthy.
 */
const MAX_CHUNKS_PER_CAPTURE = 6;
const COMMIT_SPACING_MS = 800;
/**
 * How many queued messages the session-start may replay.
 *
 * The shared default is 50, and the replay is synchronous: with a busy server
 * those 50 round trips can outlast the hook's whole budget, and the session
 * then starts with neither a profile nor a recall. The upstream limit is an
 * environment variable, and MiniMax Code filters a hook process's environment
 * down to an allowlist, so the host has to name its own budget.
 */
const PENDING_REPLAY_LIMIT = 8;
/**
 * How long the session-start replay may take.
 *
 * The count alone is not enough: on a slow server every queued entry costs a
 * round trip, and the session start has ten seconds in total. A time bound
 * keeps the replay from spending the whole budget and leaving the session
 * without a profile and without a recall.
 */
const PENDING_REPLAY_TIMEOUT_MS = 2_000;

/**
 * Split the turns to send into slices small enough for one commit.
 *
 * A single oversized turn still gets its own slice: it is better to hand the
 * model one large message than to drop it.
 */
export function splitMcodeCommitChunks(toSend, chunkChars = COMMIT_CHUNK_CHARS) {
  const chunks = [];
  let current = [];
  let size = 0;
  for (const item of toSend) {
    const length = String(item.content || "").length;
    if (current.length && size + length > chunkChars) {
      chunks.push(current);
      current = [];
      size = 0;
    }
    current.push(item);
    size += length;
  }
  if (current.length) chunks.push(current);
  return chunks;
}

export const mcode = {
  prefix: "mc-",
  tracksPendingPrompt: true,
  capturesOnlyWhenEnabled: true,
  detachesCapture: true,
  profileStage: "session-start",
  pendingReplay: PENDING_REPLAY_LIMIT,
  pendingReplayTimeoutMs: PENDING_REPLAY_TIMEOUT_MS,
  requestBudgets: {
    SessionStart: HOOK_BUDGET_MS,
    UserPromptSubmit: HOOK_BUDGET_MS,
    Stop: WRITE_BUDGET_MS,
    PreCompact: WRITE_BUDGET_MS,
    SessionEnd: WRITE_BUDGET_MS,
    SubagentStop: WRITE_BUDGET_MS,
  },
  stages: {
    SessionStart: "start",
    UserPromptSubmit: "prompt",
    Stop: "capture",
    PreCompact: "capture",
    SessionEnd: "capture",
    SubagentStop: "capture",
  },
  /**
   * A subagent reports the parent's session id, so its own turns would land in
   * the parent's memory. Re-key the payload on the agent id instead: the state
   * file, the OpenViking session and the capture cursor all become per agent.
   */
  normalizeInput: (input) => {
    if (input?.hook_event_name !== "SubagentStop") return input;
    const agentId = String(input.agent_id || "").trim();
    if (!agentId) return input;
    return { ...input, session_id: `agent-${agentId}` };
  },
  envelope(event, block) {
    if (!block) return null;
    return {
      hookSpecificOutput: {
        hookEventName: event,
        additionalContext: block,
      },
    };
  },
  /**
   * A file tool cannot open a `viking://` URI, so the call is denied with a
   * pointer to the MCP tools. A shell command that merely carries such a URI
   * is data, not a path, so it runs and the model gets a notice instead.
   */
  guard(input = {}) {
    const { toolName, toolInput } = readToolEvent(input);
    const denied = evaluateUriGuard(toolName, toolInput);
    if (denied) {
      return {
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          permissionDecision: "deny",
          permissionDecisionReason: denied.reason,
        },
      };
    }
    const notice = evaluateUriNotice(toolName, toolInput);
    if (notice) {
      return {
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          additionalContext: notice.reason,
        },
      };
    }
    return {};
  },
  prompt(input) {
    return cleanMcodeText(input.prompt || "");
  },
  async capture(ctx, state, event) {
    // A subagent has its own transcript; the parent's path would replay the
    // whole conversation into the agent's own session.
    const input = event === "SubagentStop"
      ? { ...ctx.input, transcript_path: ctx.input.agent_transcript_path || "" }
      : ctx.input;
    const transcript = buildMcodeTurns(input, state);
    if (transcript.status !== "ok") {
      ctx.log(`capture_${transcript.status}`, transcript.error ? { error: transcript.error } : {});
      return null;
    }
    const plan = buildIncrementalCapturePlan(transcript.turns, state, ctx.cfg);
    if (plan.toSend.length === 0) return null;

    // A commit that was accepted but whose extraction failed leaves messages
    // sent and uncommitted. That gap is retried before anything new is added:
    // a later commit must never overtake it, or the gap is never committed.
    let nextState = { ...state, pendingCommit: false };
    if (state.pendingCommit) {
      const retried = await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
      if (!retried.ok) {
        ctx.log("commit_retry_failed", { sessionId: ctx.sessionId });
        return { ...state, pendingCommit: true };
      }
    }

    // Send and commit in slices: one commit must stay inside the extraction
    // model's input limit, however long the conversation has grown.
    const chunks = splitMcodeCommitChunks(plan.toSend);
    let captured = 0;
    for (const [index, chunk] of chunks.entries()) {
      if (index >= MAX_CHUNKS_PER_CAPTURE) {
        // The rest stays for the next capture; the cursor only covers what
        // was sent, so nothing is skipped.
        ctx.log("capture_backlog_deferred", { remaining: chunks.length - index });
        break;
      }
      const payloads = chunk.map(({ turn, content }) => ({
        role: turn.role,
        content,
        ...(turn.turnId ? { turn_id: turn.turnId } : {}),
      }));
      const result = await addAgentMessages(ctx.fetchJSON, ctx.sessionId, payloads, ctx.peerId);
      const chunkPlan = { candidates: chunk, toSend: chunk, payloads };
      const applied = applyIncrementalCaptureResult(nextState, chunkPlan, result, cleanMcodeText);
      nextState = applied;
      captured += applied.captured;
      if (applied.captured > 0) {
        const committed = await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
        if (!committed.ok) {
          nextState = { ...nextState, pendingCommit: true };
          ctx.log("commit_failed", { sessionId: ctx.sessionId });
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, COMMIT_SPACING_MS));
      }
    }

    const { captured: _captured, ...persisted } = nextState;
    return { ...persisted, capturedSinceCommit: Number(state.capturedSinceCommit || 0) + captured };
  },
};
