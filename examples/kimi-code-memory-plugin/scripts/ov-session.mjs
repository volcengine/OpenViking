/**
 * OpenViking session transport for the Kimi Code plugin.
 *
 * Owns the send path: shaped capture turns → add-message payloads →
 * shared sendSessionMessages → ack-set cursor advancement → threshold
 * commit. Kept separate from auto-capture.mjs (the hook entry) the same way
 * the Codex plugin separates them.
 *
 * Cursor semantics (DESIGN.md §5.5/§6.5): a turnId acknowledgement set
 * (`state.capturedTurnIds`), NOT an index. wire.jsonl can be truncated by
 * kimi-code's repairWireJournal, and an index cursor would drift while the
 * ack set does not. Keys:
 *   text turn      `${turnId}:${role}`
 *   tool turn      `${turnId}:${role}:${tool_id}:${call|result}`
 *   (no turnId)    stable hash of role+content — the fallback for entries a
 *                  host could not correlate
 *
 * The cursor advances only on server confirmation; a retryable failure parks
 * the unsent payloads in the shared pending queue and leaves the cursor
 * alone, so the next hook (or the SessionStart replay) retries the same
 * records.
 */

import { isDeepStrictEqual } from "node:util";

import { stableHash } from "./shared/agent-hook-runtime.mjs";
import { sendSessionMessages } from "./shared/batch-send.mjs";
import { dequeue, listPending } from "./shared/pending-queue.mjs";

/** Stable dedup key for one shaped capture turn. */
export function turnDedupKey(turn) {
  const toolParts = (turn.parts || []).filter((part) => part?.type === "tool");
  if (toolParts.length) {
    // One key per tool part so two calls in the same turn never collide.
    return toolParts
      .map((part) => {
        const kind = part.tool_input !== undefined ? "call" : "result";
        return `${turn.turnId || "?"}:${turn.role}:${part.tool_id || "?"}:${kind}`;
      })
      .join("|");
  }
  if (turn.turnId) return `${turn.turnId}:${turn.role}`;
  return stableHash(turn.role, turn.text || "");
}

/**
 * Build add-message payloads from shared capture turns: `{ role, parts }` when
 * the turn carries structured parts (text + tool), else `{ role, content }`;
 * `turn_id` stamped when the turn carries one (shared extractCaptureTurns
 * propagates it since the Kimi decoder stamps entries); `peer_id` when a peer
 * is resolved.
 */
export function buildMessagePayloads(turns, { peerId = "" } = {}) {
  return turns.map((turn) => {
    const body = turn.parts?.length
      ? { role: turn.role, parts: turn.parts }
      : { role: turn.role, content: turn.text };
    if (turn.turnId) body.turn_id = String(turn.turnId);
    if (peerId) body.peer_id = peerId;
    return body;
  });
}

/**
 * Split shaped turns into the payloads still to send. A turn is new unless
 * every one of its dedup keys is already acked — a multi-tool turn whose
 * first call was acked but whose second was not is re-sent as a whole, which
 * the server's idempotent add-message tolerates better than dropping half a
 * turn.
 *
 * Two entries can share `turnId:role` (two assistant text steps in one turn,
 * or a rewritten turn reusing its id). The first keeps the plain key so an
 * existing ack set stays valid; later collisions get `#n`, because otherwise
 * acknowledging the first entry would silently retire the second.
 */
export function planCapture(turns, acknowledged) {
  const acked = acknowledged instanceof Set ? acknowledged : new Set(acknowledged || []);
  const seen = new Map();
  const toSend = [];
  for (const turn of turns) {
    const keys = turnDedupKey(turn).split("|").map((key) => {
      const occurrence = (seen.get(key) || 0) + 1;
      seen.set(key, occurrence);
      return occurrence === 1 ? key : `${key}#${occurrence}`;
    });
    if (keys.length && keys.every((key) => acked.has(key))) continue;
    toSend.push({ turn, keys });
  }
  return toSend;
}

/**
 * Send the planned turns, advancing the ack set only for what the server
 * confirmed. Both the batch path and its serial fallback report confirmations
 * through `onSent(n)`, so a running index maps confirmations back to the
 * exact plan entries they cover.
 */
/**
 * Remove queued copies of payloads the server just accepted. The shared send
 * path enqueues on retryable failure and acknowledges only on HTTP success,
 * so a turn queued by one stop and delivered live by the next would otherwise
 * be replayed from the queue as a second copy — the server has no turn-id
 * idempotency. Claimed (`.processing`) entries belong to their replayer and
 * are never touched here.
 */
async function dropQueuedCopies(ovSessionId, payloads) {
  if (payloads.length === 0) return 0;
  let entries;
  try {
    entries = await listPending();
  } catch {
    return 0;
  }
  let removed = 0;
  for (const { filename, entry } of entries) {
    if (entry?.sessionId !== ovSessionId || entry?.type !== "addMessage") continue;
    if (!payloads.some((payload) => isDeepStrictEqual(payload, entry.payload))) continue;
    if (await dequeue(filename)) removed += 1;
  }
  return removed;
}

export async function sendPlannedCapture(fetchJSON, ovSessionId, plan, { peerId = "", onSent } = {}) {
  if (plan.length === 0) {
    return { sent: 0, queued: 0, enqueueFailed: 0, failed: 0, retryable: false };
  }
  const payloads = buildMessagePayloads(
    plan.map((item) => item.turn),
    { peerId },
  );
  const keysByIndex = plan.map((item) => item.keys);
  const sentPayloads = [];
  // The shared sender reports successes as a running count and stops at the
  // first refusal, so `onSent(n)` always covers the next n payloads in plan
  // order — the acknowledgement set can advance by that prefix.
  let delivered = 0;
  const result = await sendSessionMessages(fetchJSON, ovSessionId, payloads, {
    enqueueOnRetryable: true,
    onSent: async (count) => {
      const end = Math.min(delivered + Math.max(0, Number(count) || 0), payloads.length);
      const keys = [];
      for (let i = delivered; i < end; i += 1) {
        keys.push(...keysByIndex[i]);
        sentPayloads.push(payloads[i]);
      }
      delivered = end;
      if (keys.length) {
        await onSent?.({ confirmed: keys, confirmedPayloads: sentPayloads.length });
      }
    },
  });
  // Sweep exactly the payloads the server accepted, never the ones still queued.
  await dropQueuedCopies(ovSessionId, sentPayloads);
  return result;
}

/**
 * Commit policy (ported from the previous adapter's
 * shouldCommitKimicodeCapture): interrupt and session-end always commit what
 * the session accumulated, even when this run confirmed nothing — SessionEnd
 * usually arrives after the last Stop already sent every turn. pre-compact
 * commits unless autoCommitOnCompact is false; otherwise the turn threshold
 * decides.
 */
export function shouldCommit(event, cfg = {}, previousCaptured = 0, captured = 0) {
  const progress = Number(previousCaptured || 0) + Number(captured || 0);
  if (progress <= 0) return false;
  if (event === "interrupt" || event === "session-end") return true;
  if (event === "pre-compact") return cfg.autoCommitOnCompact !== false;
  return progress >= Number(cfg.commitTurnThreshold || Infinity);
}

/**
 * Commit-threshold progress counts payloads (one per turn), not ack keys.
 * A multi-tool turn produces several keys, and counting those made "every N
 * turns" fire roughly once per turn. States written before this rule hold key
 * counts; the next commit resets them, so the drift is one early commit.
 */
export function advanceCapturedSinceCommit(previousCaptured, confirmedPayloads) {
  return Number(previousCaptured || 0) + Math.max(0, Number(confirmedPayloads) || 0);
}

/**
 * The turn the next decode may skip past.
 *
 * `turnOrder` lists every turn the decoder closed, and `plan` holds only the
 * entries this run still has to send, each with the ack keys it produces. A
 * turn counts as captured only when every key its entries produced is in
 * `acked`; the walk stops at the first turn that is not, so an unacknowledged
 * payload is never skipped over (it would be neither acked nor decoded again).
 * Turns with no entry were acknowledged by an earlier run and are passed.
 */
export function nextCursor(plan, acked, turnOrder) {
  const keysByTurnId = new Map();
  for (const item of plan || []) {
    const turnId = String(item.turn.turnId ?? "");
    const keys = keysByTurnId.get(turnId);
    if (keys) keys.push(...item.keys);
    else keysByTurnId.set(turnId, [...item.keys]);
  }
  let advanceTo = null;
  for (const turnId of turnOrder || []) {
    const keys = keysByTurnId.get(String(turnId));
    if (keys && !keys.every((key) => acked.has(key))) break;
    advanceTo = turnId;
  }
  return advanceTo;
}

/**
 * Whether a capture run may commit. A run is strand-free only when every
 * planned payload was confirmed or safely persisted: `failed` payloads were
 * rejected outright and exist nowhere, `queued` ones are still on disk, and
 * an older queue entry for the same session means a commit now would archive
 * across a message the earlier hook never delivered.
 */
export function canCommitCapture(result, {
  event = "stop",
  cfg = {},
  previousCaptured = 0,
  pendingCount = 0,
} = {}) {
  if (!result) return false;
  if (result.failed > 0 || result.queued > 0 || result.enqueueFailed > 0) return false;
  if (pendingCount > 0) return false;
  return shouldCommit(event, cfg, previousCaptured, result.sent);
}

/**
 * SessionEnd is the last chance to close a session. When messages are parked
 * on disk, the commit must ride the same queue behind them: SessionStart
 * replays addMessage entries before commitSession (§5.5). Payloads that were
 * lost outright (`failed`, `enqueueFailed`) must never be archived across, so
 * they suppress this rather than committing over the hole.
 */
export function shouldQueueSessionEndCommit(event, result = {}, pendingCount = 0) {
  return event === "session-end"
    && pendingCount > 0
    && (result?.failed || 0) === 0
    && (result?.enqueueFailed || 0) === 0;
}
