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

export function buildOverviewMessage(overview, firstKeptTs = 0, budget = DEFAULT_CONFIG.takeoverOverviewBudget) {
  const raw = String(overview || "");
  const truncated = truncateToTokens(raw, budget);
  const body = truncated === raw ? raw : `${truncated}\n...(truncated)`;
  const timestamp = Number.isFinite(Number(firstKeptTs)) ? Number(firstKeptTs) - 1 : 0;
  return {
    role: "user",
    content:
      `${OVERVIEW_MARKER} Earlier conversation was archived to OpenViking and summarized below. ` +
      `Use viking_search / viking_archive_expand for details.\n\n${body}`,
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

export class TakeoverCore {
  constructor({ config = {}, io = {} } = {}) {
    this.config = takeoverConfig(config);
    this.io = {
      flush: io.flush || (async () => true),
      commit: io.commit || (async () => null),
      fetchOverview: io.fetchOverview || (async () => ""),
      persistEntry: io.persistEntry || (() => {}),
      getWatermark: io.getWatermark || (() => 0),
      sleep: io.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms))),
      log: io.log || (() => {}),
    };
    this.coveredUserTurns = 0;
    this.overview = "";
    this.fingerprint = null;
    this.pendingTokens = 0;
    this.lastSeenUserTurns = 0;
    this.syncedEntryCount = 0;
    this.committing = false;
    this.lastPersisted = "";
  }

  get enabled() {
    return this.config.takeoverEnabled;
  }

  get state() {
    return {
      coveredUserTurns: this.coveredUserTurns,
      overview: this.overview,
      fingerprint: this.fingerprint,
      pendingTokens: this.pendingTokens,
      lastSeenUserTurns: this.lastSeenUserTurns,
      syncedEntryCount: this.syncedEntryCount,
      committing: this.committing,
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

      this.coveredUserTurns = Math.max(0, Math.floor(Number(data.coveredUserTurns) || 0));
      this.overview = typeof data.overview === "string" ? data.overview : "";
      this.fingerprint = typeof data.fingerprint === "string" ? data.fingerprint : null;
      this.pendingTokens = Math.max(0, Math.floor(Number(data.pendingTokens) || 0));
      this.lastSeenUserTurns = Math.max(0, Math.floor(Number(data.lastSeenUserTurns) || 0));
      this.syncedEntryCount = Math.max(0, Math.floor(Number(data.syncedEntryCount) || 0));
      this.lastPersisted = JSON.stringify(this.persistedState());
      this.log(`takeover: restored boundary at ${this.coveredUserTurns} user turns, ${this.pendingTokens} pending tokens`);
      return this.state;
    }
    return this.state;
  }

  transformContext(messages) {
    const list = Array.isArray(messages) ? messages : [];
    this.lastSeenUserTurns = countUserTurns(list);

    if (!this.enabled) return list;
    if (this.coveredUserTurns <= 0 || !this.overview) return list;

    const boundaryIdx = findBoundaryIndex(list, this.coveredUserTurns);
    if (boundaryIdx <= 0) {
      this.resetBoundary("history shorter than boundary");
      return list;
    }

    const lastCovered = list[boundaryIdx - 1];
    const fp = fingerprintMessage(lastCovered);
    if (this.fingerprint === null) {
      this.fingerprint = fp;
    } else if (this.fingerprint !== fp) {
      this.resetBoundary("fingerprint mismatch");
      return list;
    }

    const kept = list.slice(boundaryIdx);
    const firstKeptTs = typeof kept[0]?.timestamp === "number" ? kept[0].timestamp : 1;
    return [
      buildOverviewMessage(this.overview, firstKeptTs, this.config.takeoverOverviewBudget),
      ...kept,
    ];
  }

  noteSynced(estTokens) {
    if (!this.enabled) return;
    this.pendingTokens += Math.max(0, Math.floor(Number(estTokens) || 0));
  }

  /**
   * Commit iff the threshold has been crossed. Callers decide *when*: turn_end
   * only accumulates (noteSynced); the commit — which includes an overview poll
   * of up to pollMax×pollMs — runs from agent_settled / before_agent_start so
   * it never stalls an in-flight agent run.
   */
  async commitIfDue() {
    if (!this.enabled) return false;
    if (this.pendingTokens < this.config.takeoverTokenThreshold) return false;
    if (this.lastSeenUserTurns <= this.config.takeoverKeepRecentTurns) return false;
    return this.commitAndAdvance();
  }

  async onTurnSynced(estTokens) {
    this.noteSynced(estTokens);
    return this.commitIfDue();
  }

  async commitAndAdvance() {
    if (!this.enabled || this.committing) return false;
    this.committing = true;
    try {
      const flushed = await this.io.flush();
      if (!flushed) {
        this.log("takeover: flush failed; commit postponed");
        return false;
      }

      const committed = await this.io.commit({ queueOnFailure: false, keepRecentCount: this.config.takeoverKeepRecentTurns });
      if (!committed) {
        this.log("takeover: commit failed; retaining pending tokens");
        return false;
      }

      const overview = await this.pollOverview();
      if (!overview) {
        // Commit accepted but overview not ready: don't advance the boundary —
        // never inject an empty overview. Reset the token pressure so the next
        // threshold crossing retries, instead of re-committing on every turn.
        this.log("takeover: overview not ready; boundary unchanged");
        this.pendingTokens = 0;
        return false;
      }

      const newCovered = Math.max(0, this.lastSeenUserTurns - this.config.takeoverKeepRecentTurns);
      if (newCovered > this.coveredUserTurns) {
        this.coveredUserTurns = newCovered;
        this.fingerprint = null;
      }
      this.overview = overview;
      this.pendingTokens = 0;
      this.syncedEntryCount = Math.max(0, Math.floor(Number(this.io.getWatermark()) || 0));
      this.persist();
      this.log(`takeover: boundary advanced to ${this.coveredUserTurns} user turns`);
      return true;
    } finally {
      this.committing = false;
    }
  }

  async handleBeforeCompact(preparation = {}) {
    if (!this.enabled || this.committing) return undefined;
    if (!preparation.firstKeptEntryId) return undefined;

    this.committing = true;
    try {
      const flushed = await this.io.flush();
      if (!flushed) return undefined;

      const committed = await this.io.commit({ queueOnFailure: false, keepRecentCount: this.config.takeoverKeepRecentTurns });
      if (!committed) return undefined;

      const overview = await this.pollOverview();
      if (!overview) return undefined;

      this.overview = overview;
      this.resetBoundary("pi compaction absorbed boundary");
      this.pendingTokens = 0;
      this.syncedEntryCount = Math.max(0, Math.floor(Number(this.io.getWatermark()) || 0));
      this.persist();

      return {
        compaction: {
          summary: `${OVERVIEW_MARKER}\n${this.truncatedOverview()}`,
          firstKeptEntryId: preparation.firstKeptEntryId,
          tokensBefore: Number(preparation.tokensBefore) || 0,
          details: { source: "openviking" },
        },
      };
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
    if (this.coveredUserTurns !== 0 || this.fingerprint !== null) {
      this.log(`takeover: boundary reset (${reason})`);
    }
    this.coveredUserTurns = 0;
    this.fingerprint = null;
  }

  truncatedOverview() {
    const raw = String(this.overview || "");
    const truncated = truncateToTokens(raw, this.config.takeoverOverviewBudget);
    return truncated === raw ? raw : `${truncated}\n...(truncated)`;
  }

  persistedState() {
    const rawWatermark = Number(this.io.getWatermark());
    const watermark = Number.isFinite(rawWatermark)
      ? Math.max(0, Math.floor(rawWatermark))
      : this.syncedEntryCount;
    return {
      coveredUserTurns: this.coveredUserTurns,
      overview: truncateToTokens(this.overview, this.config.takeoverOverviewBudget),
      fingerprint: this.fingerprint,
      pendingTokens: this.pendingTokens,
      lastSeenUserTurns: this.lastSeenUserTurns,
      syncedEntryCount: watermark,
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

  async pollOverview() {
    for (let i = 0; i < this.config.takeoverOverviewPollMax; i++) {
      const value = await this.io.fetchOverview(this.config.takeoverOverviewBudget * 4);
      const overview = typeof value === "string"
        ? value.trim()
        : String(value?.latest_archive_overview || "").trim();
      if (overview) return overview;
      if (i < this.config.takeoverOverviewPollMax - 1 && this.config.takeoverOverviewPollMs > 0) {
        await this.io.sleep(this.config.takeoverOverviewPollMs);
      }
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
