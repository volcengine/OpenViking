/**
 * Kimi Code wire.jsonl decoder for the OpenViking memory plugin.
 *
 * Design record: DESIGN.md §5/§6. In short:
 *
 *   1. Origin filter over the full 12-kind taxonomy (wire-manifest.d.ts):
 *      capture `user` (and undefined); capture-but-mark `system_trigger` /
 *      `task` / `cron_job`; drop everything else (skill_activation,
 *      plugin_command, injection, shell_command, compaction_summary,
 *      cron_missed, hook_result, retry).
 *   2. Tool calls/results decoded into OpenAI-shaped entries so the shared
 *      capture model carries structured tool parts (§6.2). Kimi writes each
 *      call exactly once (no Codex-style duplicate representations), grouped
 *      into the assistant message of the same step (§6.3).
 *   3. Branch-chain reading (§5.4): records are read through wire-tree.mjs,
 *      a port of kimi-code's tree.ts, so turns the user undid are not
 *      captured; a corrupt tree is a skip status, never a guess.
 *   4. Protocol version check (§5.7).
 *
 * Loop event types are a closed set of five (wire-manifest.d.ts
 * ContextAppendLoopEventPayload): step.begin / step.end / content.part /
 * tool.call / tool.result. Unknown types are counted and skipped, never
 * silently mis-parsed.
 *
 * Evidence for the shapes below: the user's session wire.jsonl (90872 lines,
 * 39 turns, 397 tool calls / 397 results) — see DESIGN.md §5.
 */

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { sanitizeCapturedText } from "./shared/capture-utils.mjs";
import { readActiveChain } from "./wire-tree.mjs";

const LOOP_EVENT_TYPES = new Set([
  "step.begin",
  "step.end",
  "content.part",
  "tool.call",
  "tool.result",
]);

/** Origins that are real conversation input (captured as user messages). */
const USER_ORIGINS = new Set(["user", undefined, ""]);
/** Origins that are real prompts but not typed by the user (captured, marked). */
const SYSTEM_ORIGINS = new Set(["system_trigger", "task", "cron_job"]);
/** Everything else is host injection / summary / retry noise (dropped). */

export function cleanKimicodeText(value) {
  if (value == null) return "";
  const text = Array.isArray(value)
    ? value.map((part) => (typeof part === "string" ? part : part?.text || "")).filter(Boolean).join("\n")
    : typeof value === "object" && typeof value.text === "string"
      ? value.text
      : String(value);
  return sanitizeCapturedText(text);
}

function textFromContent(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((part) => (typeof part === "string" ? part : part?.text || ""))
    .filter(Boolean)
    .join("\n");
}

export function kimiCodeHome() {
  return process.env.KIMI_CODE_HOME || join(homedir(), ".kimi-code");
}

/**
 * Resolve the session's main-agent wire.jsonl.
 *
 * Order: session_index.jsonl (authoritative sessionDir mapping, skipping
 * deleted sessions) → scan sessions/<workspace>/<sessionId>/agents/main.
 */
export function resolveKimicodeWirePath(input = {}, home = kimiCodeHome()) {
  const sessionId = String(input.session_id || input.sessionId || "").trim();
  if (!sessionId) return "";
  const indexPath = join(home, "session_index.jsonl");
  if (existsSync(indexPath)) {
    try {
      for (const line of readFileSync(indexPath, "utf-8").split("\n")) {
        if (!line.trim()) continue;
        const row = JSON.parse(line);
        if (row.sessionId === sessionId && row.sessionDir && !row.deleted) {
          return join(row.sessionDir, "agents", "main", "wire.jsonl");
        }
      }
    } catch {
      // Fall through to the directory scan.
    }
  }
  const sessionsRoot = join(home, "sessions");
  if (!existsSync(sessionsRoot)) return "";
  try {
    for (const cwdKey of readdirSync(sessionsRoot)) {
      const candidate = join(sessionsRoot, cwdKey, sessionId, "agents", "main", "wire.jsonl");
      if (existsSync(candidate)) return candidate;
    }
  } catch {
    return "";
  }
  return "";
}

/**
 * Decode new wire records since `lastTurnId` into shared-capture-model
 * entries (OpenAI-shaped) plus the turn bookkeeping the caller needs.
 *
 * Entry shapes (consumed by shared extractCaptureTurns / collectToolNamesById):
 *   { type: "response_item", payload: { type: "message", role, content: [text] } }
 *   { type: "response_item", payload: { type: "message", role: "assistant",
 *       content: [text, {type:"tool_call",…}, …] } }          ← one step (§6.3)
 *   { type: "response_item", payload: { type: "function_call_output", call_id, output, isError?, duration_ms? } }
 *
 * Three properties the wire forces on the decoder, all learned from a real
 * 90k-line session:
 *
 *   1. `tool.result` carries NO turnId — only `parentUuid` (the call's `uuid`)
 *      and `toolCallId` (§6.1). Deriving the turn from record position instead
 *      shifts every result onto the NEXT turn (the call's turn is registered,
 *      so a positional fallback invents turn N+1), which both mis-attributes
 *      the result and lets a mid-turn host notification overwrite the user's
 *      prompt. Results are correlated through the call; an uncorrelatable
 *      result is skipped and counted, never guessed onto a turn.
 *   2. The prompt is flushed at `turn.ended` only. `context.append_message`
 *      records with a non-user origin (a finished background task, say) land
 *      between the prompt and the turn boundary; treating them as the prompt
 *      loses the user's words — kimi-code issue #5673's bug class.
 *   3. One step is one assistant message: the step's text parts and its tool
 *      calls share `stepUuid`, so they merge into that message's content array
 *      (§6.3). Tool results stay separate user-side messages, interleaved in
 *      wire order, so a turn reads user → assistant(text+call) → result →
 *      assistant… instead of a pile of calls followed by a wall of text.
 *
 * Branch chain (§5.4): records are read through wire-tree.readActiveChain, a
 * port of kimi-code's tree.ts, so turns the user undid are not captured.
 * A corrupt tree returns status "tree_error" and the caller skips the round —
 * capture must never guess.
 */
export function extractUnseenKimicodeTurns(wirePath, lastTurnId = null, options = {}) {
  if (!wirePath || !existsSync(wirePath)) return { status: "missing", entries: [], turns: [] };
  let raw;
  try {
    raw = readFileSync(wirePath, "utf-8");
  } catch (error) {
    return { status: "unreadable", entries: [], turns: [], error: error?.code || "read_failed" };
  }

  const chain = readActiveChain(raw);
  if (chain.status !== "ok") {
    return { status: chain.status, entries: [], turns: [], error: chain.error };
  }

  const entries = [];       // OpenAI-shaped entries for the shared extractor
  const order = [];         // turnIds in first-seen order
  const turnsById = new Map();
  const unknownTypes = new Set();
  /** tool.call uuid / toolCallId → the turn that issued it (result correlation). */
  const turnIdByCallUuid = new Map();
  const turnIdByToolCallId = new Map();
  let unresolvedResults = 0;
  let lateResults = 0;
  let pendingUser = "";
  let pendingSystem = "";
  let protocolVersion = null;

  const remember = (value) => {
    const turnId = String(value);
    if (!order.includes(turnId)) order.push(turnId);
    return turnId;
  };
  const ensureTurn = (value) => {
    const turnId = remember(value);
    let turn = turnsById.get(turnId);
    if (!turn) {
      turn = { user: "", system: "", items: [], closed: false };
      turnsById.set(turnId, turn);
    }
    return { turnId, turn };
  };
  const pushEntry = (payload, turnId) => {
    entries.push({ type: "response_item", payload: { ...payload, turn_id: turnId } });
  };
  // turn.prompt and the user-origin append_message carry the same text, so a
  // repeat is a no-op rather than a duplicate line.
  const offerPrompt = (text) => {
    const value = String(text || "");
    if (!value) return;
    if (!pendingUser) {
      pendingUser = value;
      return;
    }
    if (!pendingUser.includes(value)) pendingUser = `${pendingUser}\n${value}`;
  };
  const offerSystem = (text) => {
    const value = String(text || "");
    if (!value) return;
    pendingSystem = pendingSystem ? `${pendingSystem}\n${value}` : value;
  };
  const stepKeyOf = (event) => event.stepUuid ?? event.uuid ?? `step:${event.step ?? ""}`;
  /** Consecutive records of one step share its item, so text then call merge. */
  const appendStepText = (turn, key, text) => {
    const last = turn.items[turn.items.length - 1];
    if (last && last.kind === "step" && last.key === key) {
      last.text = last.text ? `${last.text}\n${text}` : text;
      return;
    }
    turn.items.push({ kind: "step", key, text, calls: [] });
  };
  const appendStepCall = (turn, key, call) => {
    const last = turn.items[turn.items.length - 1];
    if (last && last.kind === "step" && last.key === key) {
      last.calls.push(call);
      return;
    }
    turn.items.push({ kind: "step", key, text: "", calls: [call] });
  };
  /**
   * Emit a closed turn: the prompt first (it always precedes the turn's own
   * records), then its items in wire order — one assistant message per step,
   * one user-side message per tool result.
   */
  const closeTurn = (turnId, turn) => {
    if (turn.closed) return;
    turn.closed = true;
    const prompt = [turn.user, turn.system].filter(Boolean).join("\n");
    if (prompt) {
      pushEntry({ type: "message", role: "user", content: [{ type: "text", text: prompt }] }, turnId);
    }
    for (const item of turn.items) {
      if (item.kind === "step") {
        const blocks = [];
        if (item.text) blocks.push({ type: "text", text: item.text });
        for (const call of item.calls) {
          blocks.push({ type: "tool_call", call_id: call.call_id, name: call.name, arguments: call.arguments });
        }
        if (blocks.length) {
          pushEntry({ type: "message", role: "assistant", content: blocks }, turnId);
        }
        continue;
      }
      pushEntry(
        {
          type: "function_call_output",
          call_id: item.call_id,
          output: item.output,
          ...(item.isError ? { isError: true } : {}),
          ...(Number.isFinite(item.duration_ms) ? { duration_ms: item.duration_ms } : {}),
        },
        turnId,
      );
    }
  };

  for (const { record: row } of chain.entries) {
    if (row.type === "metadata") {
      protocolVersion = row.protocol_version ?? null;
      continue;
    }
    if (row.type === "context.append_message" && row.message?.role === "user") {
      const kind = row.message.origin?.kind;
      if (USER_ORIGINS.has(kind)) {
        offerPrompt(textFromContent(row.message.content));
      } else if (SYSTEM_ORIGINS.has(kind)) {
        offerSystem(textFromContent(row.message.content));
      }
      // All other origins are host injection: dropped (§5.3).
      continue;
    }
    if (row.type === "turn.prompt") {
      const kind = row.origin?.kind;
      if (USER_ORIGINS.has(kind)) offerPrompt(textFromContent(row.input));
      else if (SYSTEM_ORIGINS.has(kind)) offerSystem(textFromContent(row.input));
      continue;
    }
    if (row.type === "turn.ended") {
      const { turnId, turn } = ensureTurn(row.turnId ?? row.event?.turnId);
      if (pendingUser) {
        turn.user = turn.user ? `${turn.user}\n${pendingUser}` : pendingUser;
        pendingUser = "";
      }
      if (pendingSystem) {
        turn.system = turn.system ? `${turn.system}\n${pendingSystem}` : pendingSystem;
        pendingSystem = "";
      }
      closeTurn(turnId, turn);
      continue;
    }
    if (row.type !== "context.append_loop_event") continue;
    const event = row.event || {};
    if (!LOOP_EVENT_TYPES.has(event.type)) {
      unknownTypes.add(event.type);
      continue;
    }
    if (event.type === "content.part" && event.part?.type === "text") {
      const { turn } = ensureTurn(event.turnId);
      appendStepText(turn, stepKeyOf(event), event.part.text || "");
      continue;
    }
    if (event.type === "tool.call") {
      const { turnId, turn } = ensureTurn(event.turnId);
      appendStepCall(turn, stepKeyOf(event), {
        call_id: event.toolCallId,
        name: event.name,
        arguments: event.args ?? {},
      });
      turnIdByCallUuid.set(event.uuid, turnId);
      if (event.toolCallId) turnIdByToolCallId.set(event.toolCallId, turnId);
      continue;
    }
    if (event.type === "tool.result") {
      const turnId = turnIdByCallUuid.get(event.parentUuid) ?? turnIdByToolCallId.get(event.toolCallId);
      if (turnId === undefined) {
        unresolvedResults += 1; // orphaned result: no call to attribute it to
        continue;
      }
      const turn = turnsById.get(turnId);
      if (!turn) {
        unresolvedResults += 1;
        continue;
      }
      if (turn.closed) {
        lateResults += 1; // arrived after its turn was already emitted
        continue;
      }
      const result = event.result || {};
      turn.items.push({
        kind: "result",
        call_id: event.toolCallId,
        output: typeof result.output === "string" ? result.output : JSON.stringify(result.output ?? ""),
        ...(result.isError ? { isError: true } : {}),
        ...(Number.isFinite(result.durationMs) ? { duration_ms: result.durationMs } : {}),
      });
      continue;
    }
    // step.begin / step.end: grouping markers only, no payload (§6.3).
  }

  const lastIndex = lastTurnId == null || lastTurnId === ""
    ? -1
    : order.indexOf(String(lastTurnId));
  const turns = [];
  for (const turnId of order.slice(lastIndex + 1)) {
    const turn = turnsById.get(turnId);
    if (!turn) continue;
    const user = cleanKimicodeText([turn.user, turn.system].filter(Boolean).join("\n"));
    if (user) turns.push({ role: "user", content: user, turnId });
    const assistant = cleanKimicodeText(
      turn.items.filter((item) => item.kind === "step").map((item) => item.text).filter(Boolean).join("\n"),
    );
    if (assistant) turns.push({ role: "assistant", content: assistant, turnId });
  }
  return {
    status: "ok",
    protocolVersion,
    entries,
    turns,
    unknownTypes: [...unknownTypes],
    unresolvedResults,
    lateResults,
    activeBranch: chain.tree.activeBranch,
    branchDiagnostics: chain.tree.diagnostics,
  };
}

export function buildKimicodeTurns(input = {}, state = {}) {
  return extractUnseenKimicodeTurns(
    resolveKimicodeWirePath(input),
    state.lastTurnId || null,
  );
}
