/**
 * Capture entry point for VS Code chat turns.
 *
 * VS Code's current chat-extension API (1.99+) does NOT expose a
 * global "any participant just produced a response" event — the
 * participant's request handler IS the subscription point. So in
 * Phase 1 we wire the participant's default-path completion straight
 * into this module: when the LM stream drains, the participant calls
 * `captureChatTurn` with the user prompt + the assembled assistant
 * text and we route through the canonicaliser → CommitQueue.
 *
 * Phase 3 (#25) will plug additional sources into this same entry
 * point if/when Phase 0's spike confirms VS Code exposes a default-
 * chat response event we can subscribe to. The shape stays the same
 * regardless: take user/assistant text, canonicalise, enqueue.
 *
 * Async-detach: CommitQueue's `async` flag is sourced from
 * `cfg.writePathAsync`; when true and an asyncSpawn factory is
 * registered (later, by host setup), the actual commit RTT happens
 * in a detached worker so the user never waits.
 *
 * Bypass: handled at every layer below us — OVClient short-circuits
 * appendTurns + commit when `cfg.bypassSession` is true or any of
 * `cfg.bypassSessionPatterns` match. The queue still records the
 * call but no network work happens. We don't gate here so that the
 * debug log records the (skipped) capture attempt for observability.
 */

import {
  canonicaliseTranscript,
  type CommitQueue,
  type DebugLogger,
  type PluginConfig,
} from "@openviking/copilot-shared";

export interface CaptureChatTurnInput {
  cfg: PluginConfig;
  queue: CommitQueue;
  logger: DebugLogger;
  /** Raw user prompt text from the chat request. */
  userText: string;
  /** Assembled assistant text after the LM stream completed. */
  assistantText: string;
}

export interface CaptureChatTurnResult {
  /** Number of turns appended to OV (0 when skipped or short-circuited). */
  enqueued: number;
  /** True when the call short-circuited (autoCapture off, or empty after sanitise). */
  skipped: boolean;
  /** Whether the queue dispatched a commit as part of this enqueue. */
  triggeredCommit: boolean;
  /** Pending-token counter on the queue after this call. */
  pendingAfter: number;
}

/**
 * Take the raw user/assistant text of a completed turn and route it
 * through the canonicaliser + commit queue. Always resolves; never
 * throws to the caller (the host hot path must stay safe).
 */
export async function captureChatTurn(
  opts: CaptureChatTurnInput,
): Promise<CaptureChatTurnResult> {
  const { cfg, queue, logger } = opts;
  const child = logger.child("capture");

  if (!cfg.autoCapture) {
    child.log("skipped_disabled");
    return skipped(queue);
  }

  const turns = canonicaliseTranscript(
    [
      { role: "user", text: opts.userText },
      { role: "assistant", text: opts.assistantText },
    ],
    {
      captureAssistantTurns: cfg.captureAssistantTurns,
      captureMaxLength: cfg.captureMaxLength,
    },
  );

  if (turns.length === 0) {
    child.log("skipped_empty", {
      userLen: opts.userText.length,
      assistantLen: opts.assistantText.length,
    });
    return skipped(queue);
  }

  const res = await queue.enqueue(turns);
  child.log("captured", {
    turns: turns.length,
    appended: res.appended,
    triggeredCommit: res.triggeredCommit,
    pendingAfter: res.pendingAfter,
  });

  return {
    enqueued: res.appended,
    skipped: false,
    triggeredCommit: res.triggeredCommit,
    pendingAfter: res.pendingAfter,
  };
}

function skipped(queue: CommitQueue): CaptureChatTurnResult {
  return {
    enqueued: 0,
    skipped: true,
    triggeredCommit: false,
    pendingAfter: queue.pendingTokens,
  };
}
