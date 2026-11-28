/**
 * Decode Antigravity's `transcript_full.jsonl` into turns.
 *
 * A `PLANNER_RESPONSE` step lists its `tool_calls` (`{ name, args }`, no call
 * id), and each call's result is a later `MODEL` step (`GENERIC`, `RUN_COMMAND`,
 * …) with nothing naming the call. A planner step's calls are answered, in
 * order, by the result steps up to the next planner or user step, in
 * `step_index` order rather than line order; a call the model abandons gets
 * no result, and the next planner step starts over.
 */

import { sanitizeCapturedText } from "../../lib/capture-utils.mjs";
import { resumeTurns } from "../../lib/flow/turns.mjs";

// The host wraps a typed request as `<USER_REQUEST>…</USER_REQUEST>` followed by
// metadata blocks. Only that record is unwrapped, and only an envelope that
// opens the record, so text that merely quotes the tags is kept verbatim.
const USER_REQUEST = /^<USER_REQUEST>\s*([\s\S]*)\s*<\/USER_REQUEST>/;
const IN_PROGRESS_STATUSES = new Set(["PENDING", "QUEUED", "WAITING", "RUNNING", "GENERATING"]);
const RESULT_TYPES = new Set(["GENERIC", "RUN_COMMAND", "VIEW_FILE", "CODE_ACTION", "LIST_DIRECTORY"]);
// The header is often written twice.
const RESULT_HEADER = /^(?:Created At:[^\n]*\n(?:Completed At:[^\n]*\n)?)+/;
// A partial last line is cut inside its record, after the step index that
// every record opens with.
const PARTIAL_STEP_INDEX = /^\{"step_index":(\d+)[,}]/;

export function userRequestText(text) {
  const raw = String(text || "");
  return sanitizeCapturedText(raw.match(USER_REQUEST)?.[1] ?? raw);
}

const sameText = (text) => userRequestText(text).replace(/\s+/g, " ");

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
export function resolveTurnStep(jsonl, initialNumSteps, lastUserInput) {
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
export function latestCheckpoint(jsonl) {
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

/**
 * The transcript's steps in step order. Lines are not always written in that
 * order, and a step written again keeps only its last record. A last line still
 * being written stands in for its step as `{ step_index, held: true }`; one cut
 * before its step index holds back the whole transcript.
 */
function steps(jsonl) {
  const lines = String(jsonl || "").split("\n").filter((line) => line.trim());
  const byIndex = new Map();
  for (const [i, line] of lines.entries()) {
    let step;
    try {
      step = JSON.parse(line);
    } catch {
      if (i < lines.length - 1) continue;
      const index = line.match(PARTIAL_STEP_INDEX)?.[1];
      if (index === undefined) return [];
      step = { step_index: Number(index), held: true };
    }
    const index = Number(step?.step_index);
    if (Number.isInteger(index)) byIndex.set(index, step);
  }
  return [...byIndex.values()].sort((a, b) => a.step_index - b.step_index);
}

/**
 * MCP tools are all called through `call_mcp_tool`; the call is named after the
 * tool it reaches, the way other hosts name MCP tools, so the server can tell
 * OpenViking's own tools apart.
 */
function toolCall(call, id) {
  const name = String(call?.name || "unknown");
  const args = call?.args ?? {};
  if (name !== "call_mcp_tool" || !args.ToolName) return { id, name, input: args };
  const server = /openviking/i.test(String(args.ServerName || "")) ? "openviking" : String(args.ServerName || "unknown");
  return { id, name: `mcp__${server}__${args.ToolName}`, input: args.Arguments ?? {} };
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
 * The finished turns, in step order: a user request, or a planner step's text
 * and calls followed by the results answering them. A turn's id is the index of
 * its user or planner step.
 */
export function extractTurns(jsonl) {
  const turns = [];
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
    if (open) turns.pop();
    open = null;
  };
  for (const step of steps(jsonl)) {
    if (step.held) { reopen(); break; }
    const index = step.step_index;
    const isUser = step.type === "USER_INPUT" && step.source === "USER_EXPLICIT";
    const isPlanner = step.type === "PLANNER_RESPONSE";
    if (step.source === "MODEL" && RESULT_TYPES.has(step.type)) {
      if (step.status !== "RUNNING" && IN_PROGRESS_STATUSES.has(step.status)) { reopen(); break; }
      open?.results.push(step);
      continue;
    }
    if (!isUser && !isPlanner) continue;
    close();
    // An in-flight turn step is read again by the next Stop; terminal failures
    // (CANCELED, ERROR, INVALID, …) will never become DONE and are skipped.
    if (IN_PROGRESS_STATUSES.has(step.status)) break;
    if (step.status && step.status !== "DONE") continue;
    const turnId = String(index);
    if (isUser) {
      turns.push({ role: "user", turnId, content: userRequestText(step.content) });
      continue;
    }
    const text = typeof step.content === "string" ? step.content : "";
    const turn = {
      role: "assistant",
      turnId,
      content: text,
      payload: { role: "assistant", content: text.trim() ? [{ type: "text", text }] : [] },
    };
    const calls = (Array.isArray(step.tool_calls) ? step.tool_calls : [])
      .map((call, i) => toolCall(call, `ag-${index}-${i}`));
    turns.push(turn);
    open = { turn, calls, results: [] };
  }
  close();
  return turns;
}

/** @param {import("../../lib/flow/host.d.mts").DecodeInput} input */
export function decode({ text, cursor }) {
  // Turn ids are step indexes. State written before turns were acknowledged
  // one by one keeps the last step it read in `lastStep`.
  const last = cursor?.lastTurnId ?? null;
  const after = last !== null && last !== "" ? Number(last) : Number.isInteger(cursor?.lastStep) ? cursor.lastStep : -1;
  return resumeTurns(extractTurns(text).filter((turn) => Number(turn.turnId) > after), cursor);
}
