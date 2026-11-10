import type { OVClient } from "./client.js";
import type { OVConfig } from "./config.js";
import { buildRecallBlock } from "./shared/recall-core.mjs";

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

  constructor(
    client: OVClient,
    config: OVConfig,
    sessionId: () => string | null = () => null,
  ) {
    this.client = client;
    this.config = config;
    this.sessionId = sessionId;
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
   * Prepend this turn's recall block to the newest user message of the
   * deep-copied provider view.
   *
   * No injection ledger here: replaying historical blocks needed pi entry ids
   * from SessionManager.buildContextEntries(), which pi 0.80.3 does not
   * expose. The `<openviking-context` guard is what keeps the prefix stable —
   * an already-injected message (a recall block from an earlier copy, or a
   * frozen context-window header) is never rewritten.
   */
  injectRecall(messages: any[]): any[] {
    const block = this.cache.block;
    if (!block) return messages;

    // Locate the newest user message; everything before it is history.
    let lastUserIndex = -1;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "user") {
        lastUserIndex = i;
        break;
      }
    }
    if (lastUserIndex === -1) return messages;

    const newest = messages[lastUserIndex];
    // Idempotency: never stack a second block onto an already-injected copy.
    if (textOf(newest).includes("<openviking-context")) return messages;

    prependBlock(newest, block);
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
