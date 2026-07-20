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

  constructor(client: OVClient, config: OVConfig) {
    this.client = client;
    this.config = config;
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
      (path: string, init?: any, options?: any) => this.client.fetchJSON(path, init, 10000),
      this.config as any,
      userQuery,
      { actorPeerId: this.config.peerId },
    );
    this.cache = { block, promptText: userQuery };
    return block;
  }

  // --- Injection ---

  injectRecall(messages: any[]): any[] {
    if (!this.cache.block) return messages;

    // Find the user message (scan backwards)
    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i];
      if (msg.role === "user") {
        // Idempotency check
        const content = typeof msg.content === "string"
          ? msg.content
          : Array.isArray(msg.content)
            ? msg.content.filter((b: any) => b.type === "text").map((b: any) => b.text).join("")
            : "";

        if (content.includes("<openviking-context")) break;

        // Prepend block to user message
        const block = this.cache.block;
        if (typeof msg.content === "string") {
          msg.content = block + "\n" + msg.content;
        } else if (Array.isArray(msg.content)) {
          const textBlocks = msg.content.filter((b: any) => b.type === "text");
          if (textBlocks.length > 0) {
            (textBlocks[0] as any).text = block + "\n" + (textBlocks[0] as any).text;
          }
        }
        break;
      }
    }
    return messages;
  }

  invalidate(): void {
    this.cache = { block: null, promptText: "" };
    this.pendingPrompt = "";
  }
}
