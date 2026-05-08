/**
 * Render a ranked recall list into the `<openviking-context>` block the
 * host injects at the top of each user turn. Token-budget aware:
 * front-of-list items render as full-content lines until the budget is
 * exhausted, after which subsequent items degrade to URI-only hints
 * (instead of being dropped entirely). Mirrors the CC plugin's
 * scripts/auto-recall.mjs:buildInjectionBlock + resolveItemContent so
 * the resulting block is bit-compatible with anything that already
 * parses the CC plugin's output.
 *
 * Block shape (kept in sync with capture/sanitize.ts's stripper):
 *
 *   <openviking-context>
 *   Relevant context from OpenViking. Use the read MCP tool to expand URIs.
 *   - [memory 80%] short content fits inline
 *   - [memory 60%] viking://agent/memories/long-uri-degraded-to-hint
 *   </openviking-context>
 */

import type { RecallHit } from "../ov-client.js";
import { clampScore, estimateTokens } from "./rank.js";

const HEADER = "Relevant context from OpenViking. Use the read MCP tool to expand URIs.";
const OPEN = "<openviking-context>";
const CLOSE = "</openviking-context>";
const TYPE_FALLBACK = "item";

export interface FormatRecallBlockOptions {
  /** Token budget for full-content lines. Items beyond it degrade to URI hints. */
  tokenBudget: number;
  /** Hard cap on a single content line's char length; suffix with "...". */
  maxContentChars: number;
  /** When true, prefer the item's abstract over a level-2 fetch. */
  preferAbstract: boolean;
  /**
   * Optional resolver invoked for level=2 hits when `preferAbstract` is
   * false. Returns the body string, `null`, or throws — both `null` and
   * a thrown error fall back to the abstract → URI chain so a single
   * dead resource never breaks the whole block.
   */
  fetchContent?: (uri: string) => Promise<string | null>;
}

export interface FormatRecallBlockResult {
  /** The rendered block, ready to inject. `null` when there are no items. */
  block: string | null;
  /** Number of lines that rendered with full content (vs. URI hints). */
  contentCount: number;
  /** Number of lines that degraded to URI hints (over budget OR fetch failed). */
  hintCount: number;
  /** Tokens consumed by the content lines. */
  budgetUsed: number;
}

/**
 * Render the `<openviking-context>` block for a ranked list of hits.
 *
 * Returns `{block: null, ...}` for an empty input so the caller can skip
 * the injection cleanly without parsing an empty string.
 */
export async function formatRecallBlock<T extends RecallHit>(
  items: T[],
  opts: FormatRecallBlockOptions,
): Promise<FormatRecallBlockResult> {
  if (items.length === 0) {
    return { block: null, contentCount: 0, hintCount: 0, budgetUsed: 0 };
  }

  const budgetTotal = Math.max(0, Math.floor(opts.tokenBudget));
  let budgetRemaining = budgetTotal;

  const lines: string[] = [OPEN, HEADER];
  let contentCount = 0;
  let hintCount = 0;

  for (const item of items) {
    const score = scoreLabel(item);
    const type = typeLabel(item);
    const uriLine = `- [${type} ${score}%] ${item.uri}`;

    if (budgetRemaining > 0) {
      const content = await resolveItemContent(item, opts);
      const contentLine = `- [${type} ${score}%] ${content}`;
      const lineTokens = estimateTokens(contentLine);

      // First content item always lands even if it would overflow on its
      // own — a recall that returns one very-long memory is still more
      // useful than an empty block. Mirrors openclaw spec §6.2.
      if (lineTokens > budgetRemaining && contentCount > 0) {
        lines.push(uriLine);
        hintCount++;
      } else {
        lines.push(contentLine);
        budgetRemaining -= lineTokens;
        contentCount++;
      }
    } else {
      lines.push(uriLine);
      hintCount++;
    }
  }

  lines.push(CLOSE);
  return {
    block: lines.join("\n"),
    contentCount,
    hintCount,
    budgetUsed: budgetTotal - budgetRemaining,
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function scoreLabel(item: RecallHit): string {
  return (clampScore(item.score) * 100).toFixed(0);
}

function typeLabel(item: RecallHit): string {
  if (typeof item.type === "string" && item.type.trim()) return item.type.trim();
  return TYPE_FALLBACK;
}

async function resolveItemContent<T extends RecallHit>(
  item: T,
  opts: FormatRecallBlockOptions,
): Promise<string> {
  const abstract = String(item.abstract ?? item["overview"] ?? "").trim();

  let content: string;
  if (opts.preferAbstract && abstract) {
    content = abstract;
  } else if (item["level"] === 2 && opts.fetchContent) {
    let body: string | null = null;
    try {
      body = await opts.fetchContent(item.uri);
    } catch {
      body = null;
    }
    content = (body ?? "").trim() || abstract || item.uri;
  } else {
    content = abstract || item.uri;
  }

  return capContentChars(content, opts.maxContentChars);
}

function capContentChars(text: string, maxChars: number): string {
  const cap = Math.max(1, Math.floor(maxChars));
  if (text.length <= cap) return text;
  return `${text.slice(0, cap)}...`;
}
