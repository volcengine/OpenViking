/**
 * VS Code-free helpers for the @openviking chat participant.
 *
 * The participant adapter (`participant.ts`) imports `vscode` and
 * dispatches request.command + writes to the chat stream. The core
 * helpers below are pure-async functions that take a ParticipantState
 * and the user's input — easy to unit-test under Vitest without
 * needing @vscode/test-electron.
 */

import {
  CommitQueue,
  type DebugLogger,
  type FormatRecallBlockResult,
  type OVClient,
  type PluginConfig,
  RecallCache,
  formatRecallBlock,
  rankRecallHits,
} from "@openviking/copilot-shared";

export interface ParticipantState {
  cfg: PluginConfig;
  client: OVClient;
  cache: RecallCache;
  queue: CommitQueue;
  /** OpenViking session id derived once per workspace via session/id.ts. */
  sessionId: string;
  logger: DebugLogger;
}

export interface BuildRecallContextResult {
  /** The rendered <openviking-context> block, or null when no usable hits. */
  block: string | null;
  /** How many hits made it into the formatted block. */
  hits: number;
  /** Telemetry from the formatter (counts, budget). */
  telemetry: FormatRecallBlockResult;
}

export interface BuildRecallContextOptions {
  /**
   * Optional content resolver passed to the formatter for level=2
   * items. Hosts wire this against `OVClient` or VS Code-only
   * resolution; the helper itself stays vscode-free.
   */
  fetchContent?: (uri: string) => Promise<string | null>;
}

const SCORE_FLOOR_FOR_FORMATTER = 0;

/**
 * Run a recall against OV, rank + dedupe, format the
 * `<openviking-context>` block. Cached by (query, sessionId) so
 * back-to-back calls from the participant + LM tool round-trip OV
 * only once per turn.
 */
export async function buildRecallContext(
  state: ParticipantState,
  query: string,
  opts: BuildRecallContextOptions = {},
): Promise<BuildRecallContextResult> {
  const trimmed = query.trim();
  if (trimmed.length < state.cfg.minQueryLength) {
    state.logger.log("recall_skipped_short", { length: trimmed.length });
    return emptyResult();
  }

  if (!state.cfg.autoRecall) {
    state.logger.log("recall_skipped_disabled");
    return emptyResult();
  }

  const cacheKey = { query: trimmed, sessionId: state.sessionId };
  const recallRes = await state.cache.getOrFetch(cacheKey, () =>
    state.client.recall(trimmed, {
      limit: Math.max(state.cfg.recallLimit * 2, 8),
      sessionId: state.sessionId,
      scoreThreshold: SCORE_FLOOR_FOR_FORMATTER,
    }),
  );

  if (!recallRes.ok) {
    state.logger.log("recall_failed", { message: recallRes.error.message });
    return emptyResult();
  }

  const ranked = rankRecallHits(recallRes.value, {
    query: trimmed,
    scoreThreshold: state.cfg.scoreThreshold,
    recallLimit: state.cfg.recallLimit,
  });
  if (ranked.length === 0) {
    state.logger.log("recall_no_hits");
    return emptyResult();
  }

  const telemetry = await formatRecallBlock(ranked, {
    tokenBudget: state.cfg.recallTokenBudget,
    maxContentChars: state.cfg.recallMaxContentChars,
    preferAbstract: state.cfg.recallPreferAbstract,
    ...(opts.fetchContent ? { fetchContent: opts.fetchContent } : {}),
  });

  state.logger.log("recall_built", {
    hits: ranked.length,
    contentCount: telemetry.contentCount,
    hintCount: telemetry.hintCount,
    budgetUsed: telemetry.budgetUsed,
  });

  return { block: telemetry.block, hits: ranked.length, telemetry };
}

export interface RunResult {
  ok: boolean;
  message: string;
}

/**
 * Append a user-provided memory text into the OV session and force a
 * commit so it lands as an archived memory immediately. Used by the
 * `/store` slash command.
 */
export async function runStore(
  state: ParticipantState,
  text: string,
): Promise<RunResult> {
  const trimmed = text.trim();
  if (!trimmed) return { ok: false, message: "Nothing to store — message body was empty." };

  const enqueueRes = await state.queue.enqueue([{ role: "user", content: trimmed }]);
  if (enqueueRes.appended === 0) {
    state.logger.log("store_failed_append");
    return { ok: false, message: "Failed to append the message to OpenViking." };
  }

  // Force a flush regardless of token threshold so the memory is
  // immediately archivable on the server side.
  await state.queue.flush();
  state.logger.log("store_committed", { chars: trimmed.length });
  return {
    ok: true,
    message: `Stored ${trimmed.length} character${trimmed.length === 1 ? "" : "s"} into OpenViking memory.`,
  };
}

/**
 * Delete a viking:// URI. Used by the `/forget` slash command.
 * Returns a user-facing message; never throws so the participant
 * stream can render the result inline.
 */
export async function runForget(
  state: ParticipantState,
  rawUri: string,
): Promise<RunResult> {
  const uri = rawUri.trim();
  if (!uri) return { ok: false, message: "Usage: `/forget <viking://...>`" };
  if (!uri.startsWith("viking://")) {
    return { ok: false, message: "URI must start with `viking://`." };
  }

  const res = await state.client.forget(uri);
  if (!res.ok) {
    state.logger.log("forget_failed", { uri, message: res.error.message });
    return { ok: false, message: `Failed to delete ${uri}: ${res.error.message}` };
  }
  state.logger.log("forget_ok", { uri });
  return { ok: true, message: `Deleted ${uri}.` };
}

function emptyResult(): BuildRecallContextResult {
  return {
    block: null,
    hits: 0,
    telemetry: { block: null, contentCount: 0, hintCount: 0, budgetUsed: 0 },
  };
}
