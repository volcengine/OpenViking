import type { OVClient } from "./client.js";
import type { OVConfig } from "./config.js";
import { buildRecallBlock } from "./shared/recall-core.mjs";
import { RecallLedger, ledgerKey } from "./shared/recall-ledger.mjs";

export interface RecallCache {
  block: string | null;
  promptText: string;      // the query this cache is for
}

export class RecallManager {
  private client: OVClient;
  private config: OVConfig;
  private cache: RecallCache = { block: null, promptText: "" };
  private pendingPrompt = "";
  // Read lazily: the session manager that owns this id is constructed after the
  // recall manager, and the id only exists once a session has been opened.
  private sessionId: () => string | null;
  private ledger: RecallLedger | null;

  constructor(
    client: OVClient,
    config: OVConfig,
    sessionId: () => string | null = () => null,
    ledger: RecallLedger | null = null,
  ) {
    this.client = client;
    this.config = config;
    this.sessionId = sessionId;
    this.ledger = ledger;
  }

  /** Bind the injection ledger to the pi session (idempotent). */
  openLedger(piSessionId: string): void {
    this.ledger?.open(piSessionId);
  }

  queueSearch(userQuery: string): void {
    this.pendingPrompt = userQuery;
  }

  async searchPending(): Promise<string | null> {
    if (!this.pendingPrompt) return this.cache.block;

    const userQuery = this.pendingPrompt;
    this.pendingPrompt = "";
    if (userQuery.trim().length < this.config.minQueryLength) {
      this.cache = { block: null, promptText: userQuery };
      return null;
    }

    const block = await buildRecallBlock(
      // 10s is this extension's own budget for a bare retrieval; when the
      // request also spends a server fuse the helper hands down a longer
      // deadline, and ignoring it would abort a request still inside its fuse.
      (path: string, init?: any, options?: any) =>
        this.client.fetchJSON(path, init, options?.timeoutMs ?? 10000),
      this.config as any,
      userQuery,
      {
        actorPeerId: this.config.peerId,
        // Passing the OV session id is what turns on server-side query
        // expansion and the cross-turn dedup ledger.
        sessionId: this.sessionId() ?? "",
      },
    );
    this.cache = { block, promptText: userQuery };
    return block;
  }

  // --- Injection ---

  /**
   * Inject recall into the deep-copied provider view of the session.
   *
   * Two passes keep the request prefix byte-identical across turns (#4137):
   * historical user messages get the exact block the ledger says was sent
   * with them before, and only the newest user message receives this turn's
   * fresh block (which is then recorded for future re-injection).
   */
  injectRecall(
    messages: any[],
    messageIdFor: (message: any) => string | null = () => null,
  ): any[] {
    const ledger = this.ledger?.isOpen ? this.ledger : null;
    if (!this.cache.block && !ledger) return messages;

    // Locate the newest user message; everything before it is history.
    let lastUserIndex = -1;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "user") {
        lastUserIndex = i;
        break;
      }
    }
    if (lastUserIndex === -1) return messages;

    for (let i = 0; i <= lastUserIndex; i++) {
      const msg = messages[i];
      if (msg.role !== "user") continue;
      const content = textOf(msg);
      const isNewest = i === lastUserIndex;
      // Pi entry ids survive compaction and branch navigation. Missing ids
      // fail closed: fresh recall may still reach the newest message, but no
      // historical block is replayed or recorded under an unstable ordinal.
      const messageIdentity = messageIdFor(msg);
      const key = messageIdentity ? ledgerKey(messageIdentity, content) : null;

      // Idempotency: never stack a second block onto an already-injected copy.
      if (content.includes("<openviking-context")) {
        continue;
      }

      if (isNewest) {
        const block = this.cache.block;
        if (block) {
          prependBlock(msg, block);
          // Key by the ORIGINAL content: that is what the next turn's deep
          // copy of this message will contain.
          if (key) ledger?.record(key, block);
        }
      } else if (ledger && key) {
        const block = ledger.get(key);
        if (block) prependBlock(msg, block);
      }
    }

    ledger?.flush();
    return messages;
  }

  invalidate(): void {
    this.cache = { block: null, promptText: "" };
    this.pendingPrompt = "";
  }
}

function textOf(msg: any): string {
  return typeof msg.content === "string"
    ? msg.content
    : Array.isArray(msg.content)
      ? msg.content.filter((b: any) => b.type === "text").map((b: any) => b.text).join("")
      : "";
}

function prependBlock(msg: any, block: string): void {
  if (typeof msg.content === "string") {
    msg.content = block + "\n" + msg.content;
  } else if (Array.isArray(msg.content)) {
    const textBlocks = msg.content.filter((b: any) => b.type === "text");
    if (textBlocks.length > 0) {
      (textBlocks[0] as any).text = block + "\n" + (textBlocks[0] as any).text;
    }
  }
}
