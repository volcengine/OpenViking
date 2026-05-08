import type { DebugLogger } from "../debug/logger.js";
import type { PluginConfig } from "../config.js";
import type { OVResult, RecallHit, RecallOptions, ReadOptions } from "../ov-client.js";
import { RecallCache } from "./cache.js";
import { formatRecallBlock, type FormatRecallBlockResult } from "./format.js";
import { rankRecallHits } from "./rank.js";

export interface RecallContextClient {
  recall(query: string, opts: RecallOptions): Promise<OVResult<RecallHit[]>>;
  read(uri: string, opts?: ReadOptions): Promise<OVResult<string>>;
}

export interface RecallContextConfig extends Pick<
  PluginConfig,
  | "autoRecall"
  | "recallLimit"
  | "scoreThreshold"
  | "minQueryLength"
  | "recallMaxContentChars"
  | "recallTokenBudget"
  | "recallPreferAbstract"
> {}

export interface RecallContextState {
  cfg: RecallContextConfig;
  client: RecallContextClient;
  cache?: RecallCache;
  sessionId: string;
  logger?: Pick<DebugLogger, "log">;
}

export interface BuildRecallContextResult {
  block: string | null;
  hits: number;
  telemetry: FormatRecallBlockResult;
}

export interface BuildRecallContextOptions {
  targetUri?: string;
  fetchContent?: (uri: string) => Promise<string | null>;
}

const SCORE_FLOOR_FOR_FORMATTER = 0;

export async function buildRecallContextBlock(
  state: RecallContextState,
  query: string,
  opts: BuildRecallContextOptions = {},
): Promise<BuildRecallContextResult> {
  const trimmed = query.trim();
  if (trimmed.length < state.cfg.minQueryLength) {
    state.logger?.log("recall_skipped_short", { length: trimmed.length });
    return emptyResult();
  }

  if (!state.cfg.autoRecall) {
    state.logger?.log("recall_skipped_disabled");
    return emptyResult();
  }

  const sessionId = state.sessionId;
  const scope = opts.targetUri ? `target:${opts.targetUri}` : undefined;
  const fetchRecall = () => state.client.recall(trimmed, {
    limit: Math.max(state.cfg.recallLimit * 2, 8),
    sessionId,
    scoreThreshold: SCORE_FLOOR_FOR_FORMATTER,
    targetUri: opts.targetUri,
  });
  const recallRes = state.cache
    ? await state.cache.getOrFetch({ query: trimmed, sessionId, scope }, fetchRecall)
    : await fetchRecall();

  if (!recallRes.ok) {
    state.logger?.log("recall_failed", { message: recallRes.error.message });
    return emptyResult();
  }

  const ranked = rankRecallHits(recallRes.value, {
    query: trimmed,
    scoreThreshold: state.cfg.scoreThreshold,
    recallLimit: state.cfg.recallLimit,
  });
  if (ranked.length === 0) {
    state.logger?.log("recall_no_hits");
    return emptyResult();
  }

  const telemetry = await formatRecallBlock(ranked, {
    tokenBudget: state.cfg.recallTokenBudget,
    maxContentChars: state.cfg.recallMaxContentChars,
    preferAbstract: state.cfg.recallPreferAbstract,
    fetchContent: opts.fetchContent ?? ((uri) => readContent(state.client, uri)),
  });

  state.logger?.log("recall_built", {
    hits: ranked.length,
    contentCount: telemetry.contentCount,
    hintCount: telemetry.hintCount,
    budgetUsed: telemetry.budgetUsed,
  });

  return { block: telemetry.block, hits: ranked.length, telemetry };
}

async function readContent(client: RecallContextClient, uri: string): Promise<string | null> {
  const res = await client.read(uri);
  return res.ok ? res.value : null;
}

function emptyResult(): BuildRecallContextResult {
  return {
    block: null,
    hits: 0,
    telemetry: { block: null, contentCount: 0, hintCount: 0, budgetUsed: 0 },
  };
}
