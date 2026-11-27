import { RecallLedger, ledgerKey } from "./lib/recall-ledger.mjs";

/**
 * Where recalled context goes in pi's request. What to recall is the flow's
 * (`runStage(host, "context", ...)`); this places the block it returned on the
 * newest user message and replays earlier blocks from the ledger.
 */
export class RecallManager {
  private block: string | null = null;
  private ledger: RecallLedger | null;

  constructor(ledger: RecallLedger | null = null) {
    this.ledger = ledger;
  }

  /** Bind the injection ledger to the pi session (idempotent). */
  openLedger(piSessionId: string): void {
    this.ledger?.open(piSessionId);
  }

  /** The block this run's prompt recalled. */
  setBlock(block: string | null): void {
    this.block = block || null;
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
    if (!this.block && !ledger) return messages;

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
        const block = this.block;
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
    this.block = null;
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
