export const TAKEOVER_ENTRY_TYPE = "ov-takeover";
export const OVERVIEW_MARKER = "[OpenViking Session Context]";

const DEFAULT_CONFIG = {
  takeoverEnabled: true,
  takeoverTokenThreshold: 30000,
  takeoverKeepRecentTurns: 3,
  takeoverOverviewBudget: 3000,
  takeoverOverviewPollMs: 2000,
  takeoverOverviewPollMax: 15,
};

// pi hosts cap every extension event handler at 30s; omp logs `handler timed
// out after 30000ms`, discards the result and lets the handler run on (#5275).
// Takeover's work in one handler stays inside this budget.
export const HANDLER_BUDGET_MS = 25_000;
// What a commit plus one overview read needs; with less left, the commit waits
// for a later turn instead of running past the host's cap.
const COMMIT_RESERVE_MS = 10_000;
// One overview read (the client caps it at 5s) with room to spare.
const READ_RESERVE_MS = 5_000;
// Archive states after which its summary will not appear any more: the
// server's `.done` / `.failed.json` markers (idea from #5320).
const TERMINAL_ARCHIVE_STATES = new Set(["completed", "failed"]);

function numberOr(value, fallback) {
  const next = Number(value);
  return Number.isFinite(next) ? next : fallback;
}

function takeoverConfig(config = {}) {
  return {
    takeoverEnabled: config.takeoverEnabled !== false,
    takeoverTokenThreshold: Math.max(0, numberOr(config.takeoverTokenThreshold, DEFAULT_CONFIG.takeoverTokenThreshold)),
    takeoverKeepRecentTurns: Math.max(0, numberOr(config.takeoverKeepRecentTurns, DEFAULT_CONFIG.takeoverKeepRecentTurns)),
    takeoverOverviewBudget: Math.max(1, numberOr(config.takeoverOverviewBudget, DEFAULT_CONFIG.takeoverOverviewBudget)),
    takeoverOverviewPollMs: Math.max(0, numberOr(config.takeoverOverviewPollMs, DEFAULT_CONFIG.takeoverOverviewPollMs)),
    takeoverOverviewPollMax: Math.max(1, numberOr(config.takeoverOverviewPollMax, DEFAULT_CONFIG.takeoverOverviewPollMax)),
  };
}

function asEntry(value) {
  if (!value || typeof value !== "object") return null;
  return value.entry && typeof value.entry === "object" ? value.entry : value;
}

function isUserEntry(entry) {
  return entry?.type === "message" && isUserTurnStart(entry.message);
}

/** The role of the non-system message an entry puts into the context, or "". */
function contextRole(entry) {
  if (entry?.type === "message") {
    const role = entry.message?.role;
    return typeof role === "string" && role !== "system" ? role : "";
  }
  if (entry?.type === "custom_message") return "custom";
  if (entry?.type === "branch_summary" && entry.summary) return "branchSummary";
  return "";
}

/**
 * The branch entries pi builds the model context from, in context order —
 * pi's own `buildContextEntries` projection, reimplemented because pi < 0.86
 * does not expose it. After the latest compaction only the compaction entry,
 * the entries from its `firstKeptEntryId` (system messages excluded) and
 * everything after it remain; entries a context edit removed are dropped.
 * `getBranch()` itself still holds the compacted-away prefix and every custom
 * entry, so turn positions must never be counted on it directly.
 */
export function projectContextEntries(branch) {
  const path = (Array.isArray(branch) ? branch : []).filter((entry) => entry && typeof entry === "object");
  let compactionIdx = -1;
  for (let i = path.length - 1; i >= 0; i--) {
    if (path[i].type === "compaction") {
      compactionIdx = i;
      break;
    }
  }
  let entries = path;
  if (compactionIdx >= 0) {
    const compaction = path[compactionIdx];
    const keptIdx = path.findIndex((entry, i) => i < compactionIdx && entry.id === compaction.firstKeptEntryId);
    const kept = keptIdx >= 0
      ? path.slice(keptIdx, compactionIdx).filter((entry) => !(entry.type === "message" && entry.message?.role === "system"))
      : [];
    entries = [compaction, ...kept, ...path.slice(compactionIdx + 1)];
  }
  const removed = new Set();
  for (const entry of entries) {
    if (entry.type === "context_edit" && entry.replacement === null && typeof entry.targetId === "string") {
      removed.add(entry.targetId);
    }
  }
  return removed.size ? entries.filter((entry) => !removed.has(entry.id)) : entries;
}

/**
 * Position of a user entry's message among the `context` hook's messages.
 * Matched on pi's per-message timestamp, which context edits and other
 * extensions' content rewrites leave alone; the content fingerprint stands in
 * only when a timestamp is missing. When several messages match, the one at
 * the expected user-turn ordinal wins.
 */
function findUserMessage(messages, message, ordinal) {
  const ts = message?.timestamp;
  const byTime = typeof ts === "number" && Number.isFinite(ts);
  const fp = byTime ? "" : fingerprintMessage(message);
  let first = -1;
  let seen = 0;
  for (let i = 0; i < messages.length; i++) {
    if (!isUserTurnStart(messages[i])) continue;
    const match = byTime ? messages[i].timestamp === ts : fingerprintMessage(messages[i]) === fp;
    if (match) {
      if (seen === ordinal) return i;
      if (first < 0) first = i;
    }
    seen++;
  }
  return first;
}

function currentBranch(branchOrGetter) {
  if (typeof branchOrGetter === "function") {
    try {
      const value = branchOrGetter();
      return Array.isArray(value) ? value : [];
    } catch {
      return [];
    }
  }
  return Array.isArray(branchOrGetter) ? branchOrGetter : [];
}

function flattenValue(value) {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(flattenValue).filter(Boolean).join("");
  if (!value || typeof value !== "object") return "";
  if (typeof value.text === "string") return value.text;
  if (typeof value.input_text === "string") return value.input_text;
  if (typeof value.output_text === "string") return value.output_text;
  if (typeof value.content === "string") return value.content;
  if (Array.isArray(value.content)) return flattenValue(value.content);
  return "";
}

export function flattenContent(msg) {
  if (!msg || typeof msg !== "object") return "";
  return flattenValue(msg.content);
}

export function fingerprintMessage(msg) {
  const text = flattenContent(msg);
  return `${msg?.role || ""}:${text.length}:${text.slice(0, 200)}`;
}

export function isUserTurnStart(msg) {
  if (!msg || msg.role !== "user") return false;
  return !flattenContent(msg).startsWith(OVERVIEW_MARKER);
}

export function countUserTurns(messages) {
  let count = 0;
  for (const msg of Array.isArray(messages) ? messages : []) {
    if (isUserTurnStart(msg)) count++;
  }
  return count;
}

export function findBoundaryIndex(messages, coveredUserTurns) {
  const target = Math.max(0, Math.floor(Number(coveredUserTurns) || 0)) + 1;
  let seen = 0;
  for (let i = 0; i < (Array.isArray(messages) ? messages.length : 0); i++) {
    if (!isUserTurnStart(messages[i])) continue;
    seen++;
    if (seen === target) return i;
  }
  return -1;
}

export function estimateTokens(text) {
  const value = String(text || "");
  if (!value) return 0;
  let cjk = 0;
  let other = 0;
  for (const ch of value) {
    if (ch.codePointAt(0) >= 0x3000) cjk++;
    else other++;
  }
  return Math.ceil(cjk * 1.5 + other / 4);
}

export function truncateToTokens(text, budget) {
  const value = String(text || "");
  const limit = Math.max(0, Math.floor(Number(budget) || 0));
  if (!value || limit <= 0) return "";
  if (estimateTokens(value) <= limit) return value;

  let lo = 0;
  let hi = value.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (estimateTokens(value.slice(0, mid)) <= limit) lo = mid;
    else hi = mid - 1;
  }
  return value.slice(0, lo);
}

function partText(part) {
  if (!part || typeof part !== "object") return "";
  if (typeof part.text === "string") return part.text;
  if (part.type === "tool") {
    const payload = {
      name: part.tool_name,
      input: part.tool_input,
      output: part.tool_output,
      status: part.tool_status,
    };
    try {
      return JSON.stringify(payload);
    } catch {
      return String(part.tool_name || part.tool_output || "");
    }
  }
  return flattenValue(part);
}

export function estimatePayloadTokens(payload) {
  if (!payload || typeof payload !== "object") return 0;
  if (typeof payload.content === "string") return estimateTokens(payload.content);
  if (Array.isArray(payload.parts)) {
    return estimateTokens(payload.parts.map(partText).filter(Boolean).join("\n\n"));
  }
  if (Array.isArray(payload.content)) {
    return estimateTokens(payload.content.map(partText).filter(Boolean).join("\n\n"));
  }
  return 0;
}

export function buildOverviewMessage(
  overview,
  firstKeptTs = 0,
  budget = DEFAULT_CONFIG.takeoverOverviewBudget,
  recoveryHint = "",
) {
  const raw = String(overview || "");
  const truncated = truncateToTokens(raw, budget);
  const body = truncated === raw ? raw : `${truncated}\n...(truncated)`;
  const timestamp = Number.isFinite(Number(firstKeptTs)) ? Number(firstKeptTs) - 1 : 0;
  return {
    role: "user",
    content:
      `${OVERVIEW_MARKER} Earlier conversation was archived to OpenViking and summarized below. ` +
      `Use OpenViking for related context when its tools are available.\n\n${body}${recoveryHint}`,
    timestamp,
  };
}

export function countUndeliveredForSession(pendingEntries, sid) {
  if (!sid) return 0;
  let count = 0;
  for (const item of Array.isArray(pendingEntries) ? pendingEntries : []) {
    const entry = asEntry(item);
    if (entry?.type === "addMessage" && entry.sessionId === sid) count++;
  }
  return count;
}

/** Derive the parent history directory only for the server's archive shape. */
export function deriveHistoryUri(archiveUri) {
  const cleaned = String(archiveUri || "").trim().replace(/\/+$/, "");
  const m = /^(.*\/history)\/archive_\d+$/.exec(cleaned);
  return m ? m[1] : "";
}

/**
 * Read a commit response and decide whether it archived what this takeover
 * meant to trim.
 *
 * The boundary may only advance behind a commit that actually wrote an archive:
 * `status: "skipped"` (nothing to archive), an explicit `archived: false`, or a
 * missing `archive_uri` all mean there is nothing to point the overview at, and
 * the old `/context` summary must never be reused to fill the gap. Older server
 * builds omit `status`/`archived` but still return `archive_uri`; those are
 * accepted on the URI alone.
 */
export function commitOutcome(committed) {
  if (!committed || typeof committed !== "object") {
    return { accepted: false, reason: "no_result" };
  }
  const status = typeof committed.status === "string" ? committed.status : "";
  if (status === "skipped") {
    return { accepted: false, reason: `skipped:${committed.reason || "unknown"}` };
  }
  if (status && status !== "accepted") {
    return { accepted: false, reason: `status:${status}` };
  }
  if (committed.archived === false) {
    return { accepted: false, reason: "not_archived" };
  }
  const archiveUri =
    typeof committed.archive_uri === "string" ? committed.archive_uri.trim() : "";
  if (!archiveUri) {
    return { accepted: false, reason: "no_archive_uri" };
  }
  return {
    accepted: true,
    reason: "accepted",
    archiveUri,
    taskId: typeof committed.task_id === "string" ? committed.task_id : "",
  };
}

export class TakeoverCore {
  constructor({ config = {}, io = {} } = {}) {
    this.config = takeoverConfig(config);
    this.io = {
      flush: io.flush || (async () => true),
      // Sync the current branch before committing so the archive covers exactly
      // the messages this trim is about to drop; not every caller has synced.
      syncBranch: io.syncBranch || (async () => ({ added: 0, tokens: 0, allDelivered: true })),
      commit: io.commit || (async () => null),
      // Read the Working Memory of a SPECIFIC archive by its uri, so the summary
      // provably belongs to this commit and not to an older /context archive.
      readArchiveOverview: io.readArchiveOverview || (async () => null),
      // An archive's terminal state from its server markers: "completed",
      // "failed", "pending", or null when it cannot be asked.
      archiveState: io.archiveState || (async () => null),
      // How many messages the capture path would actually send for a slice of
      // the branch — the server keep_recent_count is a message count, not a
      // user-turn count, and it must exclude system/custom/filtered entries.
      captureCount: io.captureCount || (() => 0),
      persistEntry: io.persistEntry || (() => {}),
      getWatermark: io.getWatermark || (() => 0),
      // Messages OpenViking will never receive (4xx / retries exhausted): a
      // capture gap that must stop takeover cutting across it.
      droppedCount: io.droppedCount || (() => 0),
      availableTools: io.availableTools || (() => []),
      sleep: io.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms))),
      now: io.now || (() => Date.now()),
      log: io.log || (() => {}),
    };
    // The covered prefix ends at this pi entry, inclusive. Entry ids survive
    // restarts, compaction and branch navigation and identify one entry in
    // every view of the session, so the boundary needs no turn count or content
    // fingerprint — the same reason the recall ledger keys on them.
    this.coveredThroughEntryId = "";
    // Display only: user turns the boundary covers in the active context.
    this.coveredUserTurns = 0;
    this.overview = "";
    this.pendingTokens = 0;
    this.lastSeenUserTurns = 0;
    this.syncedEntryCount = 0;
    this.committing = false;
    this.lastPersisted = "";
    this.archiveUri = "";
    this.historyUri = "";
    // A commit that archived but whose Working Memory was not ready in time:
    // { archiveUri, taskId, historyUri, coveredThroughEntryId, coveredUserTurns,
    //   frozenTokens, nativeCompaction? }. While set, no second takeover commit
    // runs — later turns only re-check this same archive.
    this.pendingArchive = null;
    // A permanent delivery gap in this session: the archive is missing messages,
    // so the boundary must never advance past it. Survives restart.
    this.captureGap = false;
    // A count-based boundary (0.4.1 and earlier) — the first N user turns of the context, plus the
    // fingerprint of the last covered message once it was learned — converted
    // to an entry id the first time the context hook sees the session.
    this.legacyBoundary = null;
    // Whether the last context actually carried the boundary, so leaving and
    // re-entering the covered branch is logged once, not on every request.
    this.boundaryApplied = false;
  }

  get enabled() {
    return this.config.takeoverEnabled;
  }

  get state() {
    return {
      coveredThroughEntryId: this.coveredThroughEntryId,
      coveredUserTurns: this.coveredUserTurns,
      overview: this.overview,
      pendingTokens: this.pendingTokens,
      lastSeenUserTurns: this.lastSeenUserTurns,
      syncedEntryCount: this.syncedEntryCount,
      committing: this.committing,
      pendingArchive: this.pendingArchive,
      captureGap: this.captureGap,
      archiveUri: this.archiveUri,
      historyUri: this.historyUri,
    };
  }

  restore(entries) {
    for (let i = (Array.isArray(entries) ? entries.length : 0) - 1; i >= 0; i--) {
      const entry = entries[i];
      const isTakeoverEntry =
        (entry?.type === "custom" && entry.customType === TAKEOVER_ENTRY_TYPE) ||
        entry?.customType === TAKEOVER_ENTRY_TYPE ||
        entry?.type === TAKEOVER_ENTRY_TYPE;
      const data = isTakeoverEntry ? entry.data : null;
      if (!data || typeof data !== "object") continue;

      this.coveredThroughEntryId = typeof data.coveredThroughEntryId === "string" ? data.coveredThroughEntryId : "";
      this.coveredUserTurns = Math.max(0, Math.floor(Number(data.coveredUserTurns) || 0));
      this.legacyBoundary = !this.coveredThroughEntryId && this.coveredUserTurns > 0
        ? {
            coveredUserTurns: this.coveredUserTurns,
            fingerprint: typeof data.fingerprint === "string" ? data.fingerprint : null,
          }
        : null;
      this.overview = typeof data.overview === "string" ? data.overview : "";
      this.pendingTokens = Math.max(0, Math.floor(Number(data.pendingTokens) || 0));
      this.lastSeenUserTurns = Math.max(0, Math.floor(Number(data.lastSeenUserTurns) || 0));
      this.syncedEntryCount = Math.max(0, Math.floor(Number(data.syncedEntryCount) || 0));
      // New fields default to safe absences so an old persisted entry restores.
      this.pendingArchive = restorePendingArchive(data.pendingArchive);
      this.captureGap = data.captureGap === true;
      this.archiveUri = typeof data.archiveUri === "string" ? data.archiveUri : "";
      this.historyUri = typeof data.historyUri === "string" ? data.historyUri : "";
      this.lastPersisted = JSON.stringify(this.persistedState());
      this.log(
        `takeover: restored boundary ${this.coveredThroughEntryId || (this.legacyBoundary ? `at ${this.coveredUserTurns} user turns (count-based)` : "none")}` +
          `, ${this.pendingTokens} pending tokens` +
          (this.pendingArchive ? `, pending archive ${this.pendingArchive.archiveUri}` : "") +
          (this.captureGap ? ", capture gap set" : ""),
      );
      return this.state;
    }
    return this.state;
  }

  /**
   * Replace the covered prefix of the `context` hook's messages with the
   * archive overview. `branch` is pi's `getBranch()`: the boundary is an entry
   * id, found in pi's context projection of the branch and mapped onto the
   * messages through the first user turn it keeps.
   */
  transformContext(messages, branch = []) {
    const list = Array.isArray(messages) ? messages : [];
    this.lastSeenUserTurns = countUserTurns(list);

    if (!this.enabled || !this.overview) return list;
    const entries = projectContextEntries(currentBranch(branch));
    if (this.legacyBoundary) this.adoptLegacyBoundary(list, entries);
    if (!this.coveredThroughEntryId) return list;

    const cut = this.locateCut(list, entries);
    if (cut <= 0) {
      if (this.boundaryApplied) {
        this.log("takeover: covered prefix is not in the active context; sending it in full");
      }
      this.boundaryApplied = false;
      return list;
    }
    this.boundaryApplied = true;

    const kept = list.slice(cut);
    const firstKeptTs = typeof kept[0]?.timestamp === "number" ? kept[0].timestamp : 1;
    // Preserve every system message in the covered region, in its original
    // order. On pi >= 0.86 the transcript carries the base prompt and its tool
    // declarations as the leading `system` message, and mid-conversation tool
    // additions/removals, section updates and appended instructions as later
    // ones (see @earendil-works/pi-ai `getCurrentTools`/`getCurrentSystemMessage`).
    // Slicing them off with the covered turns would strip the model's tools and
    // instructions — the overview only stands in for the conversation, never for
    // the system state. On 0.80.3 the transcript has no system messages here, so
    // this preserves nothing and the behaviour is unchanged; on 0.87 the host
    // reconciles tools against the executable set on every request, so keeping
    // the existing declarations introduces no duplicate. System messages inside
    // the retained tail are left where they are, not hoisted in front.
    const coveredSystem = [];
    for (let i = 0; i < cut; i++) {
      if (list[i]?.role === "system") coveredSystem.push(list[i]);
    }
    return [
      ...coveredSystem,
      buildOverviewMessage(
        this.overview, firstKeptTs, this.config.takeoverOverviewBudget, this.recoveryHint(),
      ),
      ...kept,
    ];
  }

  /**
   * Index in `messages` of the first message kept after the covered prefix,
   * or -1 when the prefix is not in the active context (another branch, or pi
   * compacted past it) or no user turn follows it yet. It never clears the
   * boundary, so returning to the covered branch applies it again.
   */
  locateCut(messages, entries) {
    const through = entries.findIndex((entry) => entry?.id === this.coveredThroughEntryId);
    if (through < 0) return -1;
    let covered = 0;
    for (let i = 0; i <= through; i++) {
      if (isUserEntry(entries[i])) covered++;
    }
    // Messages can sit between the prefix and the next user turn: what a run
    // added after a keepRecentTurns-0 commit, or a branch summary `/tree` left
    // at the boundary. No archive covers them, so they are kept.
    const between = [];
    for (let i = through + 1; i < entries.length; i++) {
      const entry = entries[i];
      if (!isUserEntry(entry)) {
        const role = contextRole(entry);
        if (role) between.push(role);
        continue;
      }
      let cut = findUserMessage(messages, entry.message, covered);
      if (cut <= 0) return -1;
      // Step back over them role by role; the hook shows no system messages on
      // pi >= 0.87 and hoisted ones stay put anyway. A mismatch means the two
      // views disagree, and nothing is trimmed.
      for (let j = between.length - 1; j >= 0; j--) {
        do cut--; while (cut >= 0 && messages[cut]?.role === "system");
        if (cut < 0 || messages[cut]?.role !== between[j]) return -1;
      }
      if (cut > 0) this.coveredUserTurns = covered;
      return cut;
    }
    return -1;
  }

  /**
   * Convert a count-based boundary to an entry id: locate it the way 0.4.1 did — the
   * (N+1)-th user turn of the context, checked against the learned fingerprint
   * of the message before it — then take the entry in front of that turn. A
   * boundary that no longer matches is dropped, as 0.4.1 itself would have.
   */
  adoptLegacyBoundary(messages, entries) {
    const { coveredUserTurns, fingerprint } = this.legacyBoundary;
    this.legacyBoundary = null;
    const cut = findBoundaryIndex(messages, coveredUserTurns);
    if (cut <= 0 || (fingerprint !== null && fingerprintMessage(messages[cut - 1]) !== fingerprint)) {
      this.resetBoundary("count-based boundary no longer matches the context");
      return;
    }
    const users = entries.filter(isUserEntry);
    const kept = findUserMessage(users.map((entry) => entry.message), messages[cut], coveredUserTurns);
    const keptIdx = kept >= 0 ? entries.indexOf(users[kept]) : -1;
    const throughId = keptIdx > 0 ? entries[keptIdx - 1]?.id : undefined;
    if (typeof throughId !== "string" || !throughId) {
      this.resetBoundary("count-based boundary has no entry id");
      return;
    }
    this.coveredThroughEntryId = throughId;
    this.coveredUserTurns = coveredUserTurns;
    this.log(`takeover: count-based boundary adopted through entry ${throughId}`);
  }

  async onTurnSynced(estTokens, branch = [], { deadline } = {}) {
    if (!this.enabled) return false;
    // A commit already archived and is only waiting for its Working Memory:
    // account for this turn, then check that one instead of opening another.
    // Its resolution subtracts only the frozen token snapshot, leaving this new
    // pressure for the next boundary.
    this.pendingTokens += Math.max(0, Math.floor(Number(estTokens) || 0));
    if (this.pendingArchive) return this.commitAndAdvance(branch, { deadline });
    if (this.pendingTokens < this.config.takeoverTokenThreshold) return false;
    // Whether the branch has enough user turns to leave a keep-recent tail is
    // decided by freezeBoundary on pi's context projection of the branch.
    return this.commitAndAdvance(branch, { deadline });
  }

  /**
   * Check a pending archive once, outside `turn_end` — `before_agent_start`
   * calls this so a summary that finished between prompts (or between `pi -p`
   * / `pi -c` processes) trims the very next request.
   */
  async resumePending(branch = [], { deadline } = {}) {
    if (!this.enabled || this.committing || !this.pendingArchive) return false;
    const until = this.deadlineFrom(deadline);
    if (this.remaining(until) < 2 * READ_RESERVE_MS) return false;
    this.committing = true;
    try {
      return await this.resolvePendingArchive(branch);
    } catch (error) {
      this.log(`takeover: pending archive check failed (${errorMessage(error)}); boundary held`);
      return false;
    } finally {
      this.committing = false;
    }
  }

  /**
   * Advance the boundary behind a confirmed archive, or resume the archive a
   * previous attempt left pending. Never advances across a capture gap and
   * never reuses an old /context summary.
   *
   * Everything here runs inside a pi event handler, and hosts cap those at
   * 30s: omp logs `handler timed out`, drops the result and lets the handler
   * run on (#5275). So nothing waits for a summary — the archive is committed,
   * its overview read once, and later turns check it again — and the drain and
   * the commit only get what is left of `deadline`.
   */
  async commitAndAdvance(branch = [], { deadline } = {}) {
    if (!this.enabled || this.committing) return false;
    const until = this.deadlineFrom(deadline);
    this.committing = true;
    try {
      if (this.pendingArchive) return await this.resolvePendingArchive(branch);
      return await this.beginArchive(branch, until);
    } catch (error) {
      this.log(`takeover: archive preparation failed (${errorMessage(error)}); boundary held`);
      return false;
    } finally {
      this.committing = false;
    }
  }

  /** Freeze a boundary, confirm delivery, commit, and read this archive's summary once. */
  async beginArchive(branch, until) {
    if (this.captureGap) {
      this.log("takeover: capture gap present; boundary held, native compaction stays with pi");
      return false;
    }

    // One snapshot is frozen, synced and counted, so the archive and
    // keep_recent_count describe the same entries even if pi appends more
    // while this commit is in flight.
    const snapshot = currentBranch(branch);
    const frozen = this.freezeBoundary(snapshot);
    if (!frozen) {
      this.log("takeover: no advanceable boundary; commit skipped");
      return false;
    }

    const delivered = await this.confirmDelivery(snapshot, until);
    if (!delivered) return false;

    const left = this.remaining(until);
    if (left < COMMIT_RESERVE_MS) {
      this.log("takeover: handler budget spent before commit; commit postponed");
      return false;
    }
    const committed = await this.io.commit({
      queueOnFailure: false,
      keepRecentCount: frozen.keepRecentCount,
      timeoutMs: left - READ_RESERVE_MS,
    });
    const outcome = commitOutcome(committed);
    if (!outcome.accepted) {
      // No archive was written: hold the boundary, keep the token pressure so
      // the next threshold crossing retries, and never fall back to /context.
      if (outcome.reason === "no_result") {
        this.log("takeover: commit failed; boundary held");
      } else if (outcome.reason.startsWith("skipped:")) {
        this.log(`takeover: archive skipped (${outcome.reason.slice(8)}); boundary held`);
      } else {
        this.log(`takeover: commit returned no usable archive (${outcome.reason}); boundary held`);
      }
      return false;
    }

    const archive = {
      ...frozen,
      archiveUri: outcome.archiveUri,
      taskId: outcome.taskId,
      historyUri: deriveHistoryUri(outcome.archiveUri),
    };
    // Phase 2 writes the summary in the background and usually needs longer
    // than a handler may wait, so this is one read, not a poll.
    const overview = this.remaining(until) >= READ_RESERVE_MS
      ? await this.readOverviewOnce(archive.archiveUri)
      : "";
    if (overview) return this.advanceTo(branch, archive, overview);

    // Accepted but not summarized yet: remember exactly this archive and its
    // frozen boundary, keep local history, and let later turns and prompts
    // re-check the SAME archive rather than committing again.
    this.pendingArchive = {
      archiveUri: archive.archiveUri,
      taskId: archive.taskId || "",
      historyUri: archive.historyUri,
      coveredThroughEntryId: archive.coveredThroughEntryId,
      coveredUserTurns: archive.coveredUserTurns,
      frozenTokens: archive.frozenTokens,
    };
    this.persist();
    this.log(`takeover: ${archive.archiveUri} committed, Working Memory not ready; checking on later turns`);
    return false;
  }

  /**
   * Sync the snapshot, then drain the queue: an empty queue alone does not
   * prove delivery — the just-extracted turn may not have been sent yet. A
   * permanent loss on either step records the capture gap.
   */
  async confirmDelivery(snapshot, until) {
    const synced = await this.io.syncBranch(snapshot);
    if ((synced?.permanentFailures || 0) > 0 || this.io.droppedCount() > 0) {
      this.markCaptureGap();
      return false;
    }
    // A queued transient failure is allowed to proceed to the bounded drainer,
    // which gets the handler time the commit does not need; only the barrier's
    // final state proves that every current-session message reached the server.
    const flushed = await this.io.flush(Math.max(0, this.remaining(until) - COMMIT_RESERVE_MS));
    // The drain may have exhausted retries or failed to rewrite a queue entry.
    // Record that permanent gap even when an orphaned processing file keeps the
    // barrier closed.
    if (this.io.droppedCount() > 0) {
      this.markCaptureGap();
      return false;
    }
    if (!flushed) {
      this.log("takeover: flush barrier closed; commit postponed");
      return false;
    }
    return true;
  }

  /**
   * One check of the archive a prior attempt committed: advance once its
   * summary is there, and drop it once the archive is terminal without one — a
   * failed summary, or a server with Working Memory disabled, would otherwise
   * hold takeover on this archive for the rest of the session.
   */
  async resolvePendingArchive(branch) {
    const pending = this.pendingArchive;
    if (!pending) return false;
    // A branch switch or a pi compaction during the wait can take the frozen
    // prefix out of the active context; the advance is then abandoned, never
    // moved somewhere else.
    if (!pending.nativeCompaction && !this.inActiveContext(branch, pending.coveredThroughEntryId)) {
      this.pendingArchive = null;
      this.persist();
      this.log("takeover: frozen boundary no longer in history; advance abandoned");
      return false;
    }

    let overview = await this.readOverviewOnce(pending.archiveUri);
    if (!overview) {
      const state = await this.io.archiveState(pending.archiveUri);
      if (!TERMINAL_ARCHIVE_STATES.has(state)) return false;
      // The summary may have landed between the two reads.
      overview = await this.readOverviewOnce(pending.archiveUri);
      if (!overview) {
        this.pendingArchive = null;
        // Wait for fresh pressure before the next archive, so a server that
        // never writes Working Memory does not get a commit on every turn.
        this.pendingTokens = Math.max(0, this.pendingTokens - (Number(pending.frozenTokens) || 0));
        this.persist();
        this.log(`takeover: ${pending.archiveUri} ${state} without Working Memory; boundary held`);
        return false;
      }
    }

    if (pending.nativeCompaction) {
      // Pi already ran its own compaction after the earlier hook returned
      // undefined. This archive must not install a second takeover boundary
      // over Pi's new context, and the recovery hint keeps naming the archive
      // the current overview came from.
      this.pendingArchive = null;
      this.pendingTokens = Math.max(0, this.pendingTokens - (Number(pending.frozenTokens) || 0));
      this.persist();
      this.log(`takeover: native-compaction archive completed at ${pending.archiveUri}`);
      return false;
    }
    return this.advanceTo(branch, pending, overview);
  }

  /** Move the boundary to a summarized archive, re-checking the frozen prefix first. */
  advanceTo(branch, frozen, overview) {
    if (!this.inActiveContext(branch, frozen.coveredThroughEntryId)) {
      this.pendingArchive = null;
      this.persist();
      this.log("takeover: frozen boundary no longer in history; advance abandoned");
      return false;
    }

    this.coveredThroughEntryId = frozen.coveredThroughEntryId;
    this.coveredUserTurns = frozen.coveredUserTurns;
    this.legacyBoundary = null;
    this.overview = overview;
    this.archiveUri = frozen.archiveUri;
    this.historyUri = frozen.historyUri || "";
    this.pendingArchive = null;
    // Subtract only the pressure this trim froze; tokens accrued while waiting
    // for the summary belong to the next boundary and are not cleared.
    this.pendingTokens = Math.max(0, this.pendingTokens - (Number(frozen.frozenTokens) || 0));
    this.syncedEntryCount = Math.max(0, Math.floor(Number(this.io.getWatermark()) || 0));
    this.persist();
    this.log(
      `takeover: boundary advanced to ${this.coveredUserTurns} user turns ` +
        `(through ${this.coveredThroughEntryId}) via ${frozen.archiveUri}`,
    );
    return true;
  }

  inActiveContext(branch, entryId) {
    if (!entryId) return false;
    return projectContextEntries(currentBranch(branch)).some((entry) => entry?.id === entryId);
  }

  deadlineFrom(deadline) {
    const value = Number(deadline);
    return Number.isFinite(value) ? value : this.io.now() + HANDLER_BUDGET_MS;
  }

  remaining(until) {
    return until - this.io.now();
  }

  /**
   * Compute the candidate boundary on pi's context projection of the branch:
   * keep the last `takeoverKeepRecentTurns` user turns, archive the rest.
   * Returns the entry the covered prefix ends at, the exact keep_recent_count
   * and the token snapshot, or null when there is nothing new to archive.
   */
  freezeBoundary(branch) {
    const raw = currentBranch(branch);
    const entries = projectContextEntries(raw);
    const users = [];
    entries.forEach((entry, i) => {
      if (isUserEntry(entry)) users.push(i);
    });
    const keep = this.config.takeoverKeepRecentTurns;
    if (users.length <= keep) return null;

    // The prefix ends right in front of the oldest kept user turn, or at the
    // tip when nothing is kept; it then applies from the next user turn on.
    const through = keep > 0 ? users[users.length - keep] - 1 : entries.length - 1;
    const id = entries[through]?.id;
    if (typeof id !== "string" || !id) return null;
    // Only a prefix longer than the current one is worth an archive. A current
    // boundary outside the active context (another branch, or compacted away)
    // does not hold the new one back.
    if (this.coveredThroughEntryId &&
      entries.findIndex((entry) => entry?.id === this.coveredThroughEntryId) >= through) {
      return null;
    }

    // keep_recent_count is a server message count: the capture payloads the
    // branch holds after the covered prefix, in the order sync sent them —
    // system, custom and filtered entries excluded.
    const rawThrough = raw.findIndex((entry) => entry?.id === id);
    return {
      coveredThroughEntryId: id,
      coveredUserTurns: users.length - keep,
      keepRecentCount: Math.max(0, Math.floor(Number(this.io.captureCount(raw.slice(rawThrough + 1))) || 0)),
      frozenTokens: this.pendingTokens,
    };
  }

  markCaptureGap() {
    if (this.captureGap) return;
    this.captureGap = true;
    this.pendingArchive = null;
    this.persist();
    this.log("takeover: capture gap recorded; takeover disabled for this session, native compaction stays with pi");
  }

  recordCaptureGap() {
    this.markCaptureGap();
  }

  /**
   * Answer pi's compaction with this session's own archive summary. Pi needs
   * the summary now, so this is the one place that polls — but only until the
   * handler deadline. Unless it returns a compaction, it changes no takeover
   * state: pi may still cancel or fail its own compaction, and the boundary
   * must then keep applying to the uncompacted context.
   */
  async handleBeforeCompact(preparation = {}, branch = [], { deadline } = {}) {
    if (!this.enabled || this.committing) return undefined;
    if (!preparation.firstKeptEntryId) return undefined;
    // A gap means the archive is missing messages, so an OpenViking summary
    // cannot faithfully cover pi's cut — let pi compact natively.
    if (this.captureGap) return undefined;
    // A pending takeover archive covers only its frozen head; it cannot stand
    // in for the broader range pi selected. Never create a second archive while
    // the first one is unresolved.
    if (this.pendingArchive) return undefined;
    if (preparation.signal?.aborted) return undefined;

    const until = this.deadlineFrom(deadline);
    this.committing = true;
    try {
      // Sync the latest branch first; native compaction archives all captured
      // history (keepRecentCount 0) and hands pi its own firstKeptEntryId, so
      // the summary and the retained tail may overlap.
      if (!(await this.confirmDelivery(currentBranch(branch), until))) return undefined;
      if (preparation.signal?.aborted) return undefined;

      const left = this.remaining(until);
      if (left < COMMIT_RESERVE_MS) {
        this.log("takeover: handler budget spent before native compaction commit; using pi compaction");
        return undefined;
      }
      const committed = await this.io.commit({
        queueOnFailure: false, keepRecentCount: 0, timeoutMs: left - READ_RESERVE_MS,
      });
      const outcome = commitOutcome(committed);
      if (!outcome.accepted) {
        if (outcome.reason === "no_result") {
          this.log("takeover: native compaction commit failed; using pi compaction");
        } else if (outcome.reason.startsWith("skipped:")) {
          this.log(`takeover: native compaction archive skipped (${outcome.reason.slice(8)}); using pi compaction`);
        } else {
          this.log(`takeover: native compaction returned no usable archive (${outcome.reason}); using pi compaction`);
        }
        return undefined;
      }

      const historyUri = deriveHistoryUri(outcome.archiveUri);
      const overview = await this.pollArchiveOverview(outcome.archiveUri, preparation.signal, until);
      if (!overview || this.remaining(until) <= 0) {
        // The server accepted the archive, but this compaction attempt cannot
        // wait any longer. Remember it so ordinary takeover will not create a
        // second archive, and hand this compaction back to pi.
        this.pendingArchive = {
          archiveUri: outcome.archiveUri,
          taskId: outcome.taskId || "",
          historyUri,
          coveredThroughEntryId: "",
          coveredUserTurns: 0,
          frozenTokens: this.pendingTokens,
          nativeCompaction: true,
        };
        this.persist();
        this.log(`takeover: ${outcome.archiveUri} not summarized in time; using pi compaction`);
        return undefined;
      }

      this.overview = overview;
      this.archiveUri = outcome.archiveUri;
      this.historyUri = historyUri;
      this.resetBoundary("pi compaction absorbed boundary");
      this.pendingTokens = 0;
      this.syncedEntryCount = Math.max(0, Math.floor(Number(this.io.getWatermark()) || 0));
      this.persist();

      return {
        compaction: {
          summary:
            `${OVERVIEW_MARKER}\n${this.truncatedOverview()}` +
            this.recoveryHint(outcome.archiveUri, historyUri),
          firstKeptEntryId: preparation.firstKeptEntryId,
          tokensBefore: Number(preparation.tokensBefore) || 0,
          details: { source: "openviking" },
        },
      };
    } catch (error) {
      this.log(`takeover: native compaction archive failed (${errorMessage(error)}); using pi compaction`);
      return undefined;
    } finally {
      this.committing = false;
    }
  }

  async shutdown() {
    if (!this.enabled) return;
    this.syncedEntryCount = Math.max(0, Math.floor(Number(this.io.getWatermark()) || 0));
    this.persist();
  }

  resetBoundary(reason = "reset") {
    if (this.coveredThroughEntryId || this.legacyBoundary) {
      this.log(`takeover: boundary reset (${reason})`);
    }
    this.coveredThroughEntryId = "";
    this.coveredUserTurns = 0;
    this.legacyBoundary = null;
    this.boundaryApplied = false;
  }

  truncatedOverview() {
    const raw = String(this.overview || "");
    const truncated = truncateToTokens(raw, this.config.takeoverOverviewBudget);
    return truncated === raw ? raw : `${truncated}\n...(truncated)`;
  }

  /** Append a non-budgeted source recovery hint only when both required tools exist. */
  recoveryHint(archiveUri = this.archiveUri, historyUri = this.historyUri) {
    const tools = new Set(this.io.availableTools());
    if (!tools.has("openviking_list") || !tools.has("openviking_read")) return "";
    const archive = String(archiveUri || "").trim();
    const history = String(historyUri || "").trim();
    if (!archive || !history) return "";
    return (
      `\n\nArchived capture: ${archive}\n` +
      `This contains captured historical messages, not the unfiltered Pi transcript. ` +
      `Semantic search does not retrieve archive source verbatim. Use openviking_list on ${history} ` +
      `to locate archives, then openviking_read with uris=["${archive}/messages.jsonl"], ` +
      `offset and limit to read it in chunks.` +
      (tools.has("openviking_grep") ? ` Use openviking_grep only as an optional locator.` : "")
    );
  }

  persistedState() {
    const rawWatermark = Number(this.io.getWatermark());
    const watermark = Number.isFinite(rawWatermark)
      ? Math.max(0, Math.floor(rawWatermark))
      : this.syncedEntryCount;
    return {
      coveredThroughEntryId: this.coveredThroughEntryId,
      // Also what a 0.4.1 reader restores its boundary from after a downgrade.
      coveredUserTurns: this.coveredUserTurns,
      // A count-based boundary not yet adopted keeps its fingerprint across restarts.
      ...(this.legacyBoundary ? { fingerprint: this.legacyBoundary.fingerprint } : {}),
      overview: truncateToTokens(this.overview, this.config.takeoverOverviewBudget),
      pendingTokens: this.pendingTokens,
      lastSeenUserTurns: this.lastSeenUserTurns,
      syncedEntryCount: watermark,
      pendingArchive: this.pendingArchive,
      captureGap: this.captureGap,
      archiveUri: this.archiveUri,
      historyUri: this.historyUri,
    };
  }

  persist() {
    try {
      const state = this.persistedState();
      const key = JSON.stringify(state);
      if (key === this.lastPersisted) return;
      this.io.persistEntry(TAKEOVER_ENTRY_TYPE, state);
      this.lastPersisted = key;
    } catch {
      // Best effort. A missed state entry only means the next process sees full history.
    }
  }

  /** One read of an archive's `.overview.md`; "" until its non-empty body lands. */
  async readOverviewOnce(archiveUri) {
    const uri = String(archiveUri || "").trim();
    if (!uri) return "";
    try {
      const value = await this.io.readArchiveOverview(uri);
      return typeof value === "string" ? value.trim() : "";
    } catch (error) {
      this.log(`takeover: archive overview read failed for ${uri} (${errorMessage(error)})`);
      return "";
    }
  }

  /**
   * Poll one archive's `.overview.md` — `takeoverOverviewPollMax` reads,
   * `takeoverOverviewPollMs` apart — and stop early at `until`.
   */
  async pollArchiveOverview(archiveUri, signal, until = Infinity) {
    for (let i = 0; i < this.config.takeoverOverviewPollMax; i++) {
      if (signal?.aborted) return "";
      const overview = await this.readOverviewOnce(archiveUri);
      if (signal?.aborted) return "";
      if (overview) return overview;
      if (i === this.config.takeoverOverviewPollMax - 1 || this.config.takeoverOverviewPollMs <= 0) break;
      if (this.remaining(until) < this.config.takeoverOverviewPollMs + READ_RESERVE_MS) break;
      await this.io.sleep(this.config.takeoverOverviewPollMs);
    }
    return "";
  }

  log(message) {
    try {
      this.io.log(message);
    } catch {
      // Logging must not affect pi's context path.
    }
  }
}

/** Rehydrate a persisted pending-archive record, dropping malformed shapes. */
function restorePendingArchive(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const archiveUri = typeof value.archiveUri === "string" ? value.archiveUri.trim() : "";
  if (!archiveUri) return null;
  return {
    archiveUri,
    taskId: typeof value.taskId === "string" ? value.taskId : "",
    historyUri: typeof value.historyUri === "string" ? value.historyUri : "",
    coveredThroughEntryId: typeof value.coveredThroughEntryId === "string" ? value.coveredThroughEntryId : "",
    coveredUserTurns: Math.max(0, Math.floor(Number(value.coveredUserTurns) || 0)),
    frozenTokens: Math.max(0, Math.floor(Number(value.frozenTokens) || 0)),
    ...(value.nativeCompaction === true ? { nativeCompaction: true } : {}),
  };
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error || "unknown");
}
