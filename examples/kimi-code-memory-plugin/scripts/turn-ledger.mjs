/**
 * The handover ledger: what has been handed over, and what that proves.
 *
 * The first implementation counted *messages* and called the count "turns". Two
 * consequences followed, and both were wrong in a way the user would notice:
 *
 * - `queued` messages were added to the same counter, so a session could commit
 *   over messages the server had never received.
 * - Half a turn counted as much as a whole one, so a normal `Stop` could commit
 *   a `failed`/`cancelled` partial that the design only lets a forced boundary
 *   commit.
 *
 * So the state records, per native turn, what it is and what became of each of
 * its messages. The derived questions are then answered from that record:
 * a *complete* turn counts once, and only when every message in it is `sent`.
 * A `queued` message is durable locally and advances the handover cursor — the
 * design allows that — but it never counts as delivered.
 *
 * The record is bounded. Message bodies are never stored: one `stableId` and,
 * for a queued message, the queue's `dedupKey`, which is what a replay reports
 * back. That is enough to settle a specific message without keeping a
 * transcript in the state file.
 */

/** How many uncommitted turns one agent's ledger keeps. */
export const LEDGER_TURN_LIMIT = 200;

/** How many messages per turn the ledger keeps individually. */
export const LEDGER_MESSAGES_PER_TURN = 400;

/** The kind of a turn, which decides which boundary may commit it. */
export const TURN_COMPLETE = "complete";
export const TURN_TERMINAL = "terminal";
export const TURN_RECOVERY = "recovery";

export function emptyLedger() {
  return { turns: [], overflow: false, version: 1 };
}

function normalizeMessage(message) {
  return {
    id: String(message.id || ""),
    status: message.status === "sent" ? "sent" : "queued",
    ...(message.dedupKey ? { dedupKey: String(message.dedupKey) } : {}),
  };
}

/**
 * Add one handed-over turn.
 *
 * `messages` is `[{ id, status, dedupKey? }]` in the order they were handed
 * over. A turn already in the ledger is replaced rather than appended, so a
 * re-read of the same turn does not double-count it.
 */
export function addTurn(ledger, { turnId, kind, wireStart, wireEnd, messages = [], messageCount, at = Date.now() }) {
  const base = ledger || emptyLedger();
  const key = String(turnId);
  const existing = base.turns.find((turn) => turn.turnId === key);
  const incoming = messages.slice(0, LEDGER_MESSAGES_PER_TURN).map(normalizeMessage);

  // A turn can be handed over in more than one pass: a batch fails half way, a
  // retry re-sends the rest, or a boundary re-reads a turn whose messages were
  // partly queued. Merging is the only safe answer — replacing would forget
  // that the first half was already delivered and make the turn look incomplete
  // forever.
  const merged = mergeMessages(existing?.messages || [], incoming);
  const entry = {
    turnId: key,
    // A partial never becomes complete by being re-read; the more severe kind
    // wins so a forced boundary still recognises what it may commit.
    kind: kindOf(existing?.kind, kind),
    wireStart: Number.isFinite(existing?.wireStart) ? existing.wireStart : (Number.isFinite(wireStart) ? wireStart : undefined),
    wireEnd: Number.isFinite(wireEnd) ? wireEnd : existing?.wireEnd,
    messages: merged,
    // The native turn's real size, kept from whichever pass knew it. Deriving
    // it from the current batch would let a half-turn pass as complete.
    messageCount: Math.max(
      Number(existing?.messageCount) || 0,
      Number.isFinite(messageCount) ? Number(messageCount) : incoming.length,
    ),
    at,
  };
  const turns = base.turns.filter((turn) => turn.turnId !== key);
  turns.push(entry);
  const overflow = turns.length > LEDGER_TURN_LIMIT;
  return {
    ...base,
    turns: overflow ? turns.slice(turns.length - LEDGER_TURN_LIMIT) : turns,
    // An overflow is not a detail to forget: the ledger no longer proves what it
    // dropped, so a commit must wait for an explicit decision.
    overflow: base.overflow === true || overflow,
  };
}

/** Merge confirmed states for the same message, keeping the strongest. */
function mergeMessages(existing, incoming) {
  const out = existing.map((message) => ({ ...message }));
  for (const message of incoming) {
    const at = out.findIndex((current) => current.id && current.id === message.id);
    if (at === -1) {
      out.push({ ...message });
      continue;
    }
    const current = out[at];
    out[at] = {
      ...current,
      // `sent` beats `queued`; a queued message keeps whichever dedup key it
      // was first given, because that is what a replay will name.
      status: current.status === "sent" || message.status === "sent" ? "sent" : "queued",
      dedupKey: current.dedupKey || message.dedupKey,
    };
  }
  return out;
}

/** The more severe of two turn kinds. */
function kindOf(existing, incoming) {
  const rank = { [TURN_COMPLETE]: 0, [TURN_RECOVERY]: 1, [TURN_TERMINAL]: 2 };
  const a = rank[existing] ?? 0;
  const b = rank[incoming] ?? 0;
  return b > a ? incoming : (existing ?? TURN_COMPLETE);
}

/**
 * Mark one message of one turn as delivered.
 *
 * `dedupKey` is how a replay names the message it settled. A message identified
 * by its `stableId` is also accepted, for a direct send whose queue entry never
 * existed.
 */
export function settleMessage(ledger, { dedupKey, stableId, turnId } = {}) {
  if (!ledger) return ledger;
  let changed = false;
  const turns = ledger.turns.map((turn) => {
    if (turnId !== undefined && String(turn.turnId) !== String(turnId)) return turn;
    let touched = false;
    const messages = turn.messages.map((message) => {
      if (message.status === "sent") return message;
      const matches = (dedupKey && message.dedupKey === dedupKey)
        || (stableId && message.id === stableId);
      if (!matches) return message;
      touched = true;
      return { ...message, status: "sent" };
    });
    if (!touched) return turn;
    changed = true;
    return { ...turn, messages };
  });
  return changed ? { ...ledger, turns } : ledger;
}

/** Drop the turns a commit settled. */
export function clearLedger(ledger) {
  return { ...emptyLedger(), version: ledger?.version ?? 1 };
}

function turnIsFullySent(turn) {
  if (turn.messageCount === 0) return true;
  return turn.messages.length >= turn.messageCount
    && turn.messages.every((message) => message.status === "sent");
}

function turnHasQueued(turn) {
  return turn.messages.some((message) => message.status === "queued");
}

/**
 * What the ledger proves right now.
 *
 *   completeSent   fully-delivered complete turns — the only thing a normal
 *                  `Stop` may commit
 *   terminalSent   fully-delivered terminal/recovery partials — only a forced
 *                  boundary may commit these
 *   queued         messages still only on disk; a commit must wait for them
 *   overflow       the ledger dropped history, so the count is a floor, not a
 *                  proof
 */
export function summarizeLedger(ledger) {
  const turns = ledger?.turns || [];
  let completeSent = 0;
  let terminalSent = 0;
  let queued = 0;
  for (const turn of turns) {
    if (turnHasQueued(turn)) queued += turn.messages.filter((message) => message.status === "queued").length;
    if (!turnIsFullySent(turn)) continue;
    if (turn.kind === TURN_COMPLETE) completeSent += 1;
    else terminalSent += 1;
  }
  return {
    completeSent,
    terminalSent,
    queued,
    overflow: ledger?.overflow === true,
    turns: turns.length,
  };
}

/**
 * The wire range the ledger has handed over and not committed.
 *
 * The cursor may not pass this, and `accept-loss` needs it to say precisely
 * what was abandoned.
 */
export function unsettledRange(ledger) {
  const turns = (ledger?.turns || []).filter((turn) => turn.wireStart !== undefined || turn.wireEnd !== undefined);
  if (turns.length === 0) return undefined;
  return {
    wireStart: Math.min(...turns.map((turn) => turn.wireStart ?? turn.wireEnd ?? 0)),
    wireEnd: Math.max(...turns.map((turn) => turn.wireEnd ?? turn.wireStart ?? 0)),
  };
}

/**
 * The wire range and stable ids of one queued message.
 *
 * A loss record has to name what was lost. Taking the whole ledger's range
 * would claim that one dropped message spanned every uncommitted turn; the
 * message's own turn is the only honest answer.
 */
export function messageRange(ledger, { dedupKey, stableId } = {}) {
  for (const turn of ledger?.turns || []) {
    const message = turn.messages.find((item) => (
      (dedupKey && item.dedupKey === dedupKey) || (stableId && item.id === stableId)
    ));
    if (!message) continue;
    return {
      turnId: turn.turnId,
      wireStart: turn.wireStart,
      wireEnd: turn.wireEnd,
      stableIds: message.id ? [message.id] : [],
    };
  }
  return undefined;
}

/** The stable ids of everything still queued, for a loss record. */
export function queuedStableIds(ledger) {
  const ids = [];
  for (const turn of ledger?.turns || []) {
    for (const message of turn.messages) {
      if (message.status === "queued" && message.id) ids.push(message.id);
    }
  }
  return ids;
}
