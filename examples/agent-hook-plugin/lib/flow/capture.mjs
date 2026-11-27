/**
 * Capture: send the turns the host's transcript holds past the session's
 * cursor, move the cursor to the last one the server or the offline queue took,
 * and commit when it is time. Turn end, compaction, session end and a late
 * catch-up are this one function; only the commit rule tells them apart.
 */

import { readFile } from "node:fs/promises";

import {
  addAgentMessages,
  readHookState,
  withAgentHookLock,
  writeHookState,
} from "../agent-hook-runtime.mjs";
import { buildCaptureMessage, hasCaptureTrigger, isCaptureEnabled } from "../capture-utils.mjs";
import { isAuthFailure } from "../retryable.mjs";
import { commitWhenDue } from "./commit.mjs";
import { appendNote, clearNotes, readNotes } from "./notes.mjs";
import { deferContext } from "./pointer.mjs";

const BOUNDARY = { compactSoon: "compact", end: "end" };
const LOCK_WAIT_MS = 5000;
const STALE_LOCK_MS = 60_000;

export async function readTranscript(host, ref) {
  const kind = host.facts?.transcript;
  const none = { status: "ok", text: "" };
  if (!ref && kind === false) return none;
  const path = ref && host.locate ? await host.locate(ref) : ref;
  if (!path) return kind === "optional" ? none : { status: "missing" };
  try {
    return { status: "ok", text: await readFile(path, "utf8") };
  } catch (error) {
    if (kind === "optional") return none;
    // Not an empty transcript: a read that failed must not acknowledge turns
    // nobody saw.
    return { status: "unreadable", error: error?.code || "read_failed" };
  }
}

/** How many of `messages` (null where a turn has nothing to send) come before the first unsettled send. */
function settledTurns(messages, settledSends) {
  let sends = 0;
  for (let index = 0; index < messages.length; index += 1) {
    if (!messages[index]) continue;
    if (sends === settledSends) return index;
    sends += 1;
  }
  return messages.length;
}

const messageText = (message) => message.parts.filter((part) => part.type === "text").map((part) => part.text).join("\n");

/** The session message each decoded turn is sent as; null where a turn has nothing to send. */
export function turnMessages(decoded, cfg) {
  return decoded.turns.map((turn) => {
    const message = buildCaptureMessage(
      turn.payload || { role: turn.role, content: turn.content },
      turn.role,
      cfg,
      { toolNameById: decoded.toolNames },
    );
    if (!message) return message;
    return {
      ...message,
      ...(turn.turnId ? { turn_id: turn.turnId } : {}),
      ...(turn.createdAt ? { created_at: turn.createdAt } : {}),
    };
  });
}

async function captureTurns(ctx, state, stateFile) {
  const { host, cfg, log } = ctx;
  const boundary = BOUNDARY[ctx.stage] || "";
  const source = await readTranscript(host, ctx.read.transcript);
  if (source.status !== "ok") {
    log(`capture_${source.status}`, source.error ? { error: source.error } : {});
    return state;
  }
  const notes = await readNotes(stateFile);
  // Until this flow stores a cursor, the one an earlier version of the plugin
  // left: in a file of the host's own, or in the session state. What that
  // version sent may still be uncommitted, so the next boundary commits.
  const legacy = state.cursor === undefined ? host.legacyCursor?.(ctx.nativeSessionId, ctx.read) : undefined;
  if (legacy !== undefined) state = { ...state, capturedSinceCommit: Math.max(1, Number(state.capturedSinceCommit) || 0) };
  const decoded = host.decode({
    text: source.text,
    cursor: state.cursor ?? legacy ?? state,
    notes,
    payload: ctx.payload,
    cfg,
  });
  // A compaction can fire in the middle of a turn, so the last assistant
  // message may still grow and waits for the next capture, where the host's
  // transcript will still have it.
  const held = boundary === "compact" && !host.facts?.compactionDropsTranscript
    && decoded.turns.at(-1)?.role === "assistant" ? 1 : 0;
  const messages = turnMessages(decoded, cfg).slice(0, decoded.turns.length - held);
  // Keyword mode keeps a batch only when the user said something worth
  // remembering; a batch it drops is settled.
  const wanted = cfg.captureMode !== "keyword"
    || messages.some((message) => message?.role === "user" && hasCaptureTrigger(messageText(message)));
  const sends = wanted ? messages.filter(Boolean) : [];

  let sent = 0;
  let captured = 0;
  let settled = messages.length;
  if (sends.length > 0) {
    const result = await addAgentMessages(ctx.fetchJSON, ctx.sessionId, sends, ctx.peerId);
    sent = result.sent;
    captured = result.sent + result.queued;
    // Content the server refused will never land, so it is settled like a sent
    // message. What could not even be queued, or was refused for the
    // credentials, stays for the next capture.
    const unsettled = result.enqueueFailed + (isAuthFailure(result) ? result.failed : 0);
    settled = settledTurns(messages, sends.length - unsettled);
    log("capture", { sent, queued: result.queued, failed: result.failed, held: unsettled });
    ctx.outcome.capture = { sent, queued: result.queued, failed: result.failed, held: unsettled };
  }

  let next = state;
  // An adopted cursor is stored even when nothing new settled, so the earlier
  // version's file is read once.
  if (settled > 0 || legacy !== undefined) next = { ...next, cursor: decoded.cursorAfter(settled) };
  // Every turn the notes could add to is settled, so they have served.
  if (notes.length > 0 && settled > 0 && settled === decoded.turns.length) await clearNotes(stateFile);
  if (boundary || sent > 0) {
    const commit = await commitWhenDue(ctx, next, { boundary, sent, captured });
    if (Object.keys(commit).some((key) => commit[key] !== next[key])) next = { ...next, ...commit };
  }
  return next;
}

/**
 * How long a capture waits for the session lock. A boundary event that loses
 * it to a slow turn end would drop the final commit: a detached one waits up
 * to the stale-lock age, one the host is waiting for half its budget.
 */
function lockWaitMs(ctx) {
  if (!BOUNDARY[ctx.stage]) return LOCK_WAIT_MS;
  return ctx.detached ? STALE_LOCK_MS : Math.max(LOCK_WAIT_MS, ctx.budgetMs / 2);
}

export async function capture(ctx) {
  const { host } = ctx;
  const enabled = isCaptureEnabled(ctx.cfg);
  const done = await withAgentHookLock(host.id, ctx.stateKey, async (stateFile) => {
    const read = await readHookState(host.id, ctx.stateKey);
    let state = deferContext(ctx, read);
    if (enabled) {
      if (ctx.read.note) await appendNote(stateFile, ctx.read.note);
      if (ctx.stage !== "toolResult") state = await captureTurns(ctx, state, stateFile);
    }
    if (state !== read) await writeHookState(host.id, ctx.stateKey, state);
    return true;
  }, { waitMs: enabled ? lockWaitMs(ctx) : LOCK_WAIT_MS });
  if (!done) ctx.log("capture_lock_timeout");
  return {};
}
