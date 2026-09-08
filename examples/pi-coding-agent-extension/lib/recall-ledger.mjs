/**
 * Persistent per-session ledger of recall blocks injected into user messages.
 *
 * Pi's context hook hands extensions a deep copy of the session messages, so
 * a block prepended to a user message is never written back to session
 * storage. On the next request the history is rebuilt without it, and the
 * request prefix no longer matches what the provider cached last turn —
 * strict-prefix caches (DeepSeek and other OpenAI-compatible providers) then
 * miss from the first divergent token (#4137).
 *
 * The ledger fixes that on the extension side: it records exactly which block
 * was injected into which user message, keyed by the stable Pi session-entry
 * id and a hash of its ORIGINAL (un-injected) content. On every context event the
 * recorded blocks are re-applied to the historical user messages in the fresh
 * deep copy, so the bytes sent to the provider this turn are identical to the
 * bytes sent last turn, and only the newest user message extends the prefix.
 *
 * Storage follows the pending-queue convention: one JSON file per pi session
 * under `~/.openviking/pi-recall-ledger/` (0o700), overridable with
 * OPENVIKING_RECALL_LEDGER_DIR. Losing the file (fresh machine, cleanup) only
 * costs one cache miss per historical message; alignment resumes on the next
 * turn because re-injection re-records as it goes.
 */
import { mkdirSync, readFileSync, writeFileSync, renameSync, chmodSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { createHash } from "node:crypto";

const LEDGER_DIR_MODE = 0o700;
const LEDGER_FILE_MODE = 0o600;
/** Bounded so a very long session cannot grow the file without limit. */
const MAX_ENTRIES = 500;
const LEDGER_VERSION = 1;

export function defaultLedgerDir() {
  return process.env.OPENVIKING_RECALL_LEDGER_DIR
    || join(homedir(), ".openviking", "pi-recall-ledger");
}

/**
 * Key = stable Pi session-entry id + hash of the original content. Entry ids
 * survive compaction and /tree navigation, disambiguate repeated identical
 * prompts, and remain unique across branches in the same session.
 */
export function ledgerKey(messageIdentity, originalContent) {
  const digest = createHash("sha256").update(originalContent).digest("hex").slice(0, 16);
  return `${messageIdentity}:${digest}`;
}

export class RecallLedger {
  constructor(dir = defaultLedgerDir()) {
    this.dir = dir;
    this.sessionId = null;
    this.entries = new Map();
    this.loaded = false;
    this.dirty = false;
  }

  /** Bind to a pi session and load its file. Safe to call repeatedly. */
  open(sessionId) {
    if (!sessionId || sessionId === this.sessionId) return;
    this.sessionId = sessionId;
    this.entries.clear();
    this.loaded = false;
    try {
      const raw = readFileSync(this.filePath(), "utf-8");
      const data = JSON.parse(raw);
      if (data && data.version === LEDGER_VERSION && Array.isArray(data.entries)) {
        for (const e of data.entries) {
          if (typeof e?.key === "string" && typeof e?.block === "string") {
            this.entries.set(e.key, e.block);
          }
        }
      }
    } catch {
      // Missing or corrupt file: start empty. Costs one miss, never breaks pi.
    }
    this.loaded = true;
  }

  get isOpen() {
    return this.loaded && this.sessionId !== null;
  }

  get(key) {
    return this.entries.get(key) ?? null;
  }

  /** Record an injected block. Call flush() once per context event afterwards. */
  record(key, block) {
    if (this.entries.get(key) === block) return;
    this.entries.set(key, block);
    while (this.entries.size > MAX_ENTRIES) {
      const oldest = this.entries.keys().next().value;
      if (oldest === undefined) break;
      this.entries.delete(oldest);
    }
    this.dirty = true;
  }

  /** Atomic write (tmp + rename), same durability pattern as pending-queue. */
  flush() {
    if (!this.dirty || !this.sessionId) return;
    try {
      mkdirSync(this.dir, { recursive: true, mode: LEDGER_DIR_MODE });
      try { chmodSync(this.dir, LEDGER_DIR_MODE); } catch { /* pre-existing dir */ }
      const payload = JSON.stringify({
        version: LEDGER_VERSION,
        entries: [...this.entries].map(([key, block]) => ({ key, block })),
      });
      const tmp = this.filePath() + ".tmp";
      writeFileSync(tmp, payload, { mode: LEDGER_FILE_MODE });
      renameSync(tmp, this.filePath());
      this.dirty = false;
    } catch {
      // Best effort; persistence failure degrades to the pre-ledger behaviour.
    }
  }

  filePath() {
    // Session ids are uuids today; sanitize anyway so a hostile id cannot escape the dir.
    const safe = String(this.sessionId).replace(/[^A-Za-z0-9._-]/g, "_");
    return join(this.dir, `${safe}.json`);
  }
}
