/**
 * Standalone diagnostic for the recall path (issue #29).
 *
 * Drives the same code path the participant + LM tool + CLI MCP
 * recall tool use, but prints intermediate steps so a user can see
 * WHY a recall returns zero hits, the wrong hits, or a degraded
 * block. Mirrors `examples/claude-code-memory-plugin/scripts/
 * debug-recall.mjs` with the same tone — verbose, sectioned text,
 * targeting eyeballs not machines.
 *
 * Designed to be vscode-free and host-free: takes an injectable
 * `client` + `output` so the CLI bin can wire it through `runMain`
 * and unit tests can exercise it with a stubbed OVClient.
 */

import type { PluginConfig } from "../config.js";
import type {
  OVResult,
  OVClient,
  RecallHit,
  RecallOptions,
} from "../ov-client.js";
import { formatRecallBlock } from "../recall/format.js";
import {
  buildQueryProfile,
  clampScore,
  rankItem,
  rankRecallHits,
} from "../recall/rank.js";

/** Subset of OVClient the debugger actually invokes. */
export type RecallDebuggerClient = Pick<OVClient, "health" | "recall">;

export interface DebugRecallArgs {
  query: string;
  /** Defaults to a synthetic id so the run doesn't accidentally write to a real session. */
  sessionId?: string;
  targetUri?: string;
}

export interface DebugRecallDeps {
  cfg: PluginConfig;
  client: RecallDebuggerClient;
  /** Buffered output sink. Defaults to a string accumulator. */
  write?: (chunk: string) => void;
}

export interface DebugRecallResult {
  exitCode: number;
  /** Captured output (also streamed via `write` when provided). */
  output: string;
}

const SEPARATOR = "─".repeat(60);

/**
 * Run the diagnostic. Always resolves; failures are written into the
 * report and surfaced via a non-zero exit code, but never thrown.
 */
export async function runDebugRecall(
  args: DebugRecallArgs,
  deps: DebugRecallDeps,
): Promise<DebugRecallResult> {
  const lines: string[] = [];
  let exitCode = 0;

  const write = (chunk: string) => {
    lines.push(chunk);
    deps.write?.(chunk);
  };
  const writeLn = (chunk = "") => write(`${chunk}\n`);

  writeLn("=== OpenViking debug-recall ===");
  writeLn();

  // ----- Config snapshot -----
  writeLn("Configuration");
  for (const line of formatConfig(deps.cfg)) writeLn(`  ${line}`);
  writeLn();

  // ----- Health -----
  writeLn("Health check");
  const health = await deps.client.health();
  if (!health.ok) {
    writeLn(`  ERROR: ${health.error.message}${health.error.status ? ` (HTTP ${health.error.status})` : ""}`);
    writeLn("  → continuing; recall may fail downstream");
    exitCode = 1;
  } else {
    writeLn(`  OK ${jsonInline(health.value)}`);
  }
  writeLn();

  // ----- Query profile -----
  const profile = buildQueryProfile(args.query);
  writeLn("Query");
  writeLn(`  prompt        : ${JSON.stringify(args.query)}`);
  writeLn(`  trimmedLen    : ${args.query.trim().length}`);
  writeLn(`  tokens        : [${profile.tokens.join(", ")}]`);
  writeLn(`  wantsTemporal : ${profile.wantsTemporal}`);
  writeLn(`  wantsPreference: ${profile.wantsPreference}`);
  writeLn();

  // ----- Recall request -----
  const sessionId = args.sessionId ?? "cp-debug";
  const recallOpts: RecallOptions = {
    limit: Math.max(deps.cfg.recallLimit * 2, 8),
    sessionId,
    scoreThreshold: 0,
    ...(args.targetUri ? { targetUri: args.targetUri } : {}),
  };
  writeLn("Recall request (POST /api/v1/search/find)");
  writeLn(`  body          : ${JSON.stringify({
    query: args.query,
    limit: recallOpts.limit,
    score_threshold: recallOpts.scoreThreshold,
    target_uri: recallOpts.targetUri,
    session_id: recallOpts.sessionId,
  })}`);
  const recallRes = await deps.client.recall(args.query, recallOpts);
  if (!recallRes.ok) {
    writeLn(`  ERROR: ${recallRes.error.message}${recallRes.error.status ? ` (HTTP ${recallRes.error.status})` : ""}`);
    writeLn();
    writeLn("Cannot continue: recall failed.");
    return { exitCode: exitCode || 2, output: lines.join("") };
  }
  writeLn(`  flat hits     : ${recallRes.value.length}`);
  writeLn();

  // ----- Ranked list -----
  writeLn("Ranked");
  const ranked = rankRecallHits(recallRes.value, {
    query: args.query,
    scoreThreshold: deps.cfg.scoreThreshold,
    recallLimit: deps.cfg.recallLimit,
  });
  if (ranked.length === 0) {
    writeLn(`  (no hits at or above scoreThreshold ${deps.cfg.scoreThreshold})`);
  } else {
    for (const [i, hit] of ranked.entries()) {
      const baseScore = clampScore(hit.score);
      const finalRank = rankItem(hit, profile);
      const boost = (finalRank - baseScore).toFixed(3);
      writeLn(
        `  ${String(i + 1).padStart(2)}. ${hit.uri}  ` +
          `[${hit.type ?? "item"}]  base=${baseScore.toFixed(2)}  rank=${finalRank.toFixed(2)}  boost=+${boost}`,
      );
    }
  }
  writeLn();

  // ----- Final block -----
  writeLn("Final <openviking-context> block");
  const block = await formatRecallBlock(ranked, {
    tokenBudget: deps.cfg.recallTokenBudget,
    maxContentChars: deps.cfg.recallMaxContentChars,
    preferAbstract: deps.cfg.recallPreferAbstract,
  });
  writeLn(`  ${SEPARATOR}`);
  if (!block.block) {
    writeLn(`  (empty — caller would NOT inject anything)`);
  } else {
    for (const line of block.block.split("\n")) writeLn(`  ${line}`);
  }
  writeLn(`  ${SEPARATOR}`);
  writeLn();

  // ----- Telemetry -----
  writeLn("Telemetry");
  writeLn(`  contentCount  : ${block.contentCount}`);
  writeLn(`  hintCount     : ${block.hintCount}`);
  writeLn(`  budgetUsed    : ${block.budgetUsed} / ${deps.cfg.recallTokenBudget}`);
  if (block.block) writeLn(`  block bytes   : ${Buffer.byteLength(block.block, "utf8")}`);

  return { exitCode, output: lines.join("") };
}

function formatConfig(cfg: PluginConfig): string[] {
  return [
    `baseUrl           : ${cfg.baseUrl}`,
    `agentId           : ${cfg.agentId}`,
    `accountId         : ${cfg.accountId || "<unset>"}`,
    `userId            : ${cfg.userId || "<unset>"}`,
    `apiKey            : ${cfg.apiKey ? `<set, ${cfg.apiKey.length} chars>` : "<unset>"}`,
    `autoRecall        : ${cfg.autoRecall}`,
    `recallLimit       : ${cfg.recallLimit}`,
    `scoreThreshold    : ${cfg.scoreThreshold}`,
    `minQueryLength    : ${cfg.minQueryLength}`,
    `recallTokenBudget : ${cfg.recallTokenBudget}`,
    `recallMaxContentChars: ${cfg.recallMaxContentChars}`,
    `recallPreferAbstract : ${cfg.recallPreferAbstract}`,
    `bypassSession     : ${cfg.bypassSession}`,
  ];
}

function jsonInline(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "(null)";
  try {
    return JSON.stringify(value);
  } catch {
    return "(unserialisable)";
  }
}

/** Re-export for tests that want to compute expected ranks. */
export const __test__ = { rankItem, formatConfig };

/** Type re-export so the CLI bin doesn't need to import OVResult separately. */
export type { OVResult, RecallHit };
