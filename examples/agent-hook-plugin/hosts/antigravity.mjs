/**
 * Antigravity adapter (the Antigravity desktop app and the `agy` CLI share it).
 *
 * Antigravity calls PreInvocation before every model call, including each call
 * inside a tool loop (`invocationNum` > 0) and each reactive wakeup for a
 * background task, subagent message, or Stop continuation (`invocationNum` 0
 * with a higher `initialNumSteps`). Recall runs only when `invocationNum` is 0
 * and no `MODEL` step has run since the latest `USER_INPUT` step in
 * `transcriptPath`, keyed by that user step so a turn injects once. When the
 * new turn's user step is not in the transcript yet, `lastUserInput` differing
 * from the last recorded request marks it as a new turn. Injected
 * context goes back as a `systemMessage` step, which stays in the conversation.
 * Stop carries no message text: capture reads the finished steps of
 * `transcriptPath` after the last captured step.
 *
 * A `PLANNER_RESPONSE` step lists its `tool_calls` (`{ name, args }`, no call
 * id), and each call's result is a later `MODEL` step (`GENERIC`, `RUN_COMMAND`,
 * …) with nothing naming the call. A planner step's calls are answered, in
 * order, by the result steps before the next planner or user step; a call the
 * model abandons gets no result, and the next planner step starts over.
 *
 * PreToolUse hands over `toolCall.name` (`read_file`, `run_command`, …) and
 * `toolCall.args`, whose keys are PascalCase (`Path`, `CommandLine`).
 */

import { existsSync, readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { basename, dirname, join } from "node:path";

import { addAgentMessages, commitAgentSession } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { buildCaptureMessage, sanitizeCapturedText } from "../../memory-plugin-shared/lib/capture-utils.mjs";
import { evaluateUriGuard } from "../../memory-plugin-shared/lib/uri-guard.mjs";

// The host wraps a typed request as `<USER_REQUEST>…</USER_REQUEST>` followed by
// metadata blocks. Only that record is unwrapped, and only an envelope that
// opens the record, so text that merely quotes the tags is kept verbatim.
const USER_REQUEST = /^<USER_REQUEST>\s*([\s\S]*)\s*<\/USER_REQUEST>/;
const IN_PROGRESS_STATUSES = new Set(["PENDING", "QUEUED", "WAITING", "RUNNING", "GENERATING"]);
const RESULT_TYPES = new Set(["GENERIC", "RUN_COMMAND", "VIEW_FILE", "CODE_ACTION", "LIST_DIRECTORY"]);
const RESULT_HEADER = /^Created At:[^\n]*\n(?:Completed At:[^\n]*\n)?/;

function userRequestText(text) {
  const raw = String(text || "");
  return sanitizeCapturedText(raw.match(USER_REQUEST)?.[1] ?? raw);
}

const sameText = (text) => userRequestText(text).replace(/\s+/g, " ");

/**
 * The host may hand over `transcript.jsonl`, whose step contents are cut short;
 * the untruncated `transcript_full.jsonl` beside it is read instead when present.
 */
export function antigravityTranscriptPath(transcriptPath) {
  if (!transcriptPath) return "";
  if (basename(transcriptPath) !== "transcript.jsonl") return transcriptPath;
  const full = join(dirname(transcriptPath), "transcript_full.jsonl");
  return existsSync(full) ? full : transcriptPath;
}

/**
 * Return `step_index + 1` of the unanswered `USER_INPUT` step before
 * `initialNumSteps`, `null` when a `MODEL` step has already run since the last
 * user step (a background-task, subagent, or Stop wakeup), or `undefined` when
 * the transcript has no parsed steps yet.
 *
 * With `lastUserInput`, a `null` becomes `undefined` when that input differs
 * from the last user step's text: the host has not written the new turn's
 * `USER_INPUT` step yet, so this is a new turn, not a wakeup.
 */
export function resolveAntigravityTurnStep(jsonl, initialNumSteps, lastUserInput) {
  const limit = Number.isInteger(initialNumSteps) && initialNumSteps > 0 ? initialNumSteps : Infinity;
  let lastUserStep = -1;
  let lastUserText = "";
  let modelRanSinceUser = false;
  let sawStep = false;
  for (const line of String(jsonl || "").split("\n")) {
    if (!line.trim()) continue;
    let step;
    try { step = JSON.parse(line); } catch { break; }
    const index = Number(step.step_index);
    if (!Number.isInteger(index)) continue;
    if (index >= limit) break;
    sawStep = true;
    if (step.type === "USER_INPUT" && step.source === "USER_EXPLICIT") {
      lastUserStep = index;
      // A truncated record (transcript.jsonl keeps only the head and tail) can
      // not be compared, so it never marks a new turn.
      lastUserText = Array.isArray(step.truncated_fields) && step.truncated_fields.includes("content")
        ? null
        : sameText(step.content);
      modelRanSinceUser = false;
    } else if (lastUserStep >= 0 && step.source === "MODEL") {
      modelRanSinceUser = true;
    }
  }
  if (!sawStep) return undefined;
  if (lastUserStep < 0 || modelRanSinceUser) {
    const pending = sameText(lastUserInput);
    return pending && lastUserText !== null && pending !== lastUserText ? undefined : null;
  }
  return lastUserStep + 1;
}

/**
 * The step index of the latest compaction. Antigravity has no compaction hook;
 * each compaction writes a `CHECKPOINT` step, and the context before it, the
 * session-start profile included, is gone.
 */
export function latestAntigravityCheckpoint(jsonl) {
  let latest;
  for (const line of String(jsonl || "").split("\n")) {
    if (!line.includes('"CHECKPOINT"')) continue;
    try {
      const step = JSON.parse(line);
      if (step.type === "CHECKPOINT" && step.source === "SYSTEM") latest = step.step_index;
    } catch { /* a partial last line */ }
  }
  return latest;
}

function toolResultBlock(step, call) {
  return {
    type: "tool_result",
    tool_use_id: call.id,
    name: call.name,
    content: String(step.content ?? "").replace(RESULT_HEADER, "") || String(step.error ?? ""),
    ...(step.status && step.status !== "DONE" ? { is_error: true } : {}),
  };
}

/**
 * The finished steps after `afterStep` as turns: a user request, or a planner
 * step's text and calls followed by the results answering them. `firstStep` is
 * the step a turn starts at, where a resend has to read from again.
 */
export function buildAntigravityTurns(jsonl, afterStep = -1) {
  const turns = [];
  let lastStep = afterStep;
  let open = null;
  const close = () => {
    if (!open) return;
    const { turn, calls, results } = open;
    // Only a balanced set of results can be paired by position; with more or
    // fewer there is no telling which call a result answers, so the calls go
    // out unanswered.
    const answered = results.length === calls.length ? results : [];
    turn.payload.content.push(
      ...calls.map(({ id, name, input }) => ({ type: "tool_use", id, name, input })),
      // A backgrounded command stays RUNNING and never reports a result.
      ...answered.flatMap((step, i) => step.status === "RUNNING" ? [] : [toolResultBlock(step, calls[i])]),
    );
    open = null;
  };
  // A result still being written belongs to the open planner step, which is
  // read again whole by the next Stop.
  const reopen = () => {
    if (!open) return;
    turns.splice(turns.indexOf(open.turn), 1);
    lastStep = open.turn.firstStep - 1;
    open = null;
  };
  for (const line of String(jsonl || "").split("\n")) {
    if (!line.trim()) continue;
    let step;
    try { step = JSON.parse(line); } catch { reopen(); break; }
    const index = Number(step.step_index);
    if (!(index > afterStep)) continue;
    const isUser = step.type === "USER_INPUT" && step.source === "USER_EXPLICIT";
    const isPlanner = step.type === "PLANNER_RESPONSE";
    if (step.source === "MODEL" && RESULT_TYPES.has(step.type)) {
      if (step.status !== "RUNNING" && IN_PROGRESS_STATUSES.has(step.status)) { reopen(); break; }
      open?.results.push(step);
      lastStep = index;
      continue;
    }
    if (!isUser && !isPlanner) {
      lastStep = index;
      continue;
    }
    close();
    // An in-flight turn step is read again by the next Stop; terminal failures
    // (CANCELED, ERROR, INVALID, …) will never become DONE and are skipped.
    if (IN_PROGRESS_STATUSES.has(step.status)) break;
    lastStep = index;
    if (step.status && step.status !== "DONE") continue;
    if (isUser) {
      turns.push({ role: "user", firstStep: index, content: userRequestText(step.content) });
      continue;
    }
    const content = typeof step.content === "string" && step.content.trim()
      ? [{ type: "text", text: step.content }]
      : [];
    const turn = { role: "assistant", firstStep: index, payload: { role: "assistant", content } };
    const calls = (Array.isArray(step.tool_calls) ? step.tool_calls : []).map((call, i) => ({
      id: `ag-${index}-${i}`,
      name: String(call?.name || "unknown"),
      input: call?.args ?? {},
    }));
    turns.push(turn);
    open = { turn, calls, results: [] };
  }
  close();
  return { turns, lastStep };
}

// File tools name their location in an argument ending in Path, File or
// Directory (`Path`, `AbsolutePath`, `TargetFile`, `DirectoryPath`). A file body
// or a command line that mentions a viking:// URI is not a location.
const LOCATION_ARG = /(?:Path|File|Directory)$/;
const GUARD_KINDS = [[/write/, "write"], [/replace|edit/, "edit"], [/grep|search/, "grep"], [/list|find/, "glob"]];

export const antigravity = {
  prefix: "ag-",
  capturesOnlyWhenEnabled: true,
  detachesCapture: true,
  requestBudgets: { "session-start": 25_000, "pre-invocation": 17_000, stop: 25_000 },
  stages: { "session-start": "start", "pre-invocation": "prompt", stop: "capture" },
  envelope(event, block) {
    return block ? { injectSteps: [{ systemMessage: { systemMessage: block } }] } : {};
  },
  normalizeInput: (input) => ({
    ...input,
    session_id: input.conversationId,
  }),
  guard(input = {}) {
    const { name = "", args } = input.toolCall || {};
    const location = Object.entries(args || {}).find(([key, value]) => (
      LOCATION_ARG.test(key) && typeof value === "string" && /^\s*viking:\/\//i.test(value)
    ))?.[1];
    if (!location) return {};
    const kind = GUARD_KINDS.find(([pattern]) => pattern.test(name))?.[1] || "read";
    const denied = evaluateUriGuard(kind, { path: location.trim() });
    // No decision at all when the call may proceed: "allow" would skip the
    // user's own permission prompt.
    return denied ? { decision: "deny", reason: denied.reason } : {};
  },
  prompt(input) {
    if (Number(input.invocationNum || 0) !== 0) return "";
    const text = sanitizeCapturedText(input.lastUserInput);
    if (!text) return "";
    let turnStep;
    if (input.transcriptPath) {
      try {
        const jsonl = readFileSync(antigravityTranscriptPath(input.transcriptPath), "utf8");
        turnStep = resolveAntigravityTurnStep(jsonl, input.initialNumSteps, text);
        input.latestCompaction = latestAntigravityCheckpoint(jsonl);
      } catch {
        turnStep = undefined;
      }
    }
    if (turnStep === null) return "";
    // Key by the user step rather than initialNumSteps, which advances on wakeups.
    input.prompt_id = `${input.conversationId}:${turnStep ?? input.initialNumSteps}`;
    return text;
  },
  async capture(ctx, state) {
    const afterStep = Number.isInteger(state.lastStep) ? state.lastStep : -1;
    const path = antigravityTranscriptPath(ctx.input.transcriptPath);
    const jsonl = path ? await readFile(path, "utf8").catch(() => "") : "";
    const { turns, lastStep } = buildAntigravityTurns(jsonl, afterStep);
    const outgoing = [];
    for (const turn of turns) {
      if (turn.role === "assistant" && ctx.cfg.captureAssistantTurns === false) continue;
      const message = buildCaptureMessage(turn.payload || { role: turn.role, content: turn.content }, turn.role, ctx.cfg);
      if (message) outgoing.push({ firstStep: turn.firstStep, message });
      else ctx.log("capture_skip", { role: turn.role, step: turn.firstStep });
    }
    const result = outgoing.length
      ? await addAgentMessages(ctx.fetchJSON, ctx.sessionId, outgoing.map((item) => item.message), ctx.peerId)
      : { sent: 0, queued: 0 };
    const captured = result.sent + result.queued;
    if (captured > 0) await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
    // The server takes a prefix of the batch: resume at the first turn it did not take.
    return {
      ...state,
      lastStep: captured < outgoing.length ? outgoing[captured].firstStep - 1 : lastStep,
    };
  },
};
