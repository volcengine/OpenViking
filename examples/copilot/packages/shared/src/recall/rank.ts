/**
 * Recall ranking pipeline (pure functions).
 *
 * 1:1 port of the ranking logic in
 * `examples/claude-code-memory-plugin/scripts/auto-recall.mjs` (lines
 * 40-112), originally lifted from `openclaw-plugin/memory-ranking.ts`.
 * Preserving the math + boost magnitudes is intentional so the Copilot
 * plugins behave identically to the Claude Code plugin against the same
 * fixtures.
 *
 * The pipeline is:
 *   1. filter raw hits by `clampScore(it.score) >= scoreThreshold`
 *   2. sort descending by `rankItem(it, profile)` (V8 stable sort
 *      preserves input order on ties — that's the contract)
 *   3. dedupe by abstract (or by URI for event/case-typed items)
 *   4. truncate to `recallLimit`
 *
 * Token budget (recallTokenBudget) is intentionally NOT enforced here —
 * that's the formatter's job (`recall/format.ts`, issue #7). The budget
 * mechanic needs per-item content resolution (which may HTTP-fetch for
 * level=2 items), so it doesn't fit a "pure function" boundary. The
 * formatter consumes whatever this module returns.
 */

import type { RecallHit } from "../ov-client.js";

// ---------------------------------------------------------------------------
// Constants — copied verbatim from auto-recall.mjs to keep behaviour parity
// ---------------------------------------------------------------------------

const PREFERENCE_QUERY_RE =
  /prefer|preference|favorite|favourite|like|偏好|喜欢|爱好|更倾向/i;
const TEMPORAL_QUERY_RE =
  /when|what time|date|day|month|year|yesterday|today|tomorrow|last|next|什么时候|何时|哪天|几月|几年|昨天|今天|明天/i;
const QUERY_TOKEN_RE = /[a-z0-9一-龥]{2,}/gi;
const STOPWORDS = new Set<string>([
  "what", "when", "where", "which", "who", "whom", "whose", "why", "how", "did", "does",
  "is", "are", "was", "were", "the", "and", "for", "with", "from", "that", "this", "your", "you",
]);

const LEAF_BOOST = 0.12;
const EVENT_BOOST = 0.1;
const PREF_BOOST = 0.08;
const OVERLAP_MAX = 0.2;
const OVERLAP_TOKEN_CAP = 8; // matches `tokens.slice(0, 8)` in the source
const OVERLAP_DENOM_CAP = 4; // matches `Math.min(tokens.length, 4)` in the source

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface QueryProfile {
  /** Lowercased, stopword-filtered tokens from the user query. */
  tokens: string[];
  /** True when the query mentions a preference cue. */
  wantsPreference: boolean;
  /** True when the query mentions a temporal cue. */
  wantsTemporal: boolean;
}

export interface RankRecallOptions {
  /** Original user query, used to derive the QueryProfile. */
  query: string;
  /** Drop hits whose clamped base score is below this. Range [0, 1]. */
  scoreThreshold: number;
  /** Maximum number of hits to keep after sort + dedupe. */
  recallLimit: number;
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/** Clamp a possibly-undefined score into [0, 1]; non-numeric/NaN → 0. */
export function clampScore(v: unknown): number {
  if (typeof v !== "number" || Number.isNaN(v)) return 0;
  return Math.max(0, Math.min(1, v));
}

/**
 * Char-based token-count estimate. Mirrors openclaw-plugin's
 * heuristic (chars/4). Shared with `recall/format.ts` so both speak the
 * same units when ranker output and formatter budget interact.
 */
export function estimateTokens(text: string | undefined | null): number {
  if (!text) return 0;
  return Math.ceil(text.length / 4);
}

/**
 * Tokenise the query, filter stopwords, detect preference + temporal
 * intent. Pure of side effects.
 */
export function buildQueryProfile(query: string): QueryProfile {
  const text = (query ?? "").trim();
  const all = text.toLowerCase().match(QUERY_TOKEN_RE) ?? [];
  const tokens = all.filter((t) => !STOPWORDS.has(t));
  return {
    tokens,
    wantsPreference: PREFERENCE_QUERY_RE.test(text),
    wantsTemporal: TEMPORAL_QUERY_RE.test(text),
  };
}

/**
 * Lexical overlap heuristic: how many of the (capped) query tokens
 * appear in `text`. Bounded to [0, OVERLAP_MAX].
 */
export function lexicalOverlapBoost(tokens: string[], text: string): number {
  if (tokens.length === 0 || !text) return 0;
  const haystack = ` ${text.toLowerCase()} `;
  let matched = 0;
  for (const token of tokens.slice(0, OVERLAP_TOKEN_CAP)) {
    if (haystack.includes(token)) matched += 1;
  }
  return Math.min(
    OVERLAP_MAX,
    (matched / Math.min(tokens.length, OVERLAP_DENOM_CAP)) * OVERLAP_MAX,
  );
}

/** True for hits whose category or URI looks like events / cases. */
export function isEventOrCaseItem(item: RecallHit): boolean {
  const cat = String(item["category"] ?? "").toLowerCase();
  const uri = String(item.uri ?? "").toLowerCase();
  return cat === "events" || cat === "cases" || uri.includes("/events/") || uri.includes("/cases/");
}

/**
 * Compute the ranking score for a single hit. Higher is better.
 * Composition: clamped base score + leaf/event/preference/lexical boosts.
 */
export function rankItem(item: RecallHit, profile: QueryProfile): number {
  const base = clampScore(item.score);
  const abstract = String(item.abstract ?? item["overview"] ?? "").trim();
  const cat = String(item["category"] ?? "").toLowerCase();
  const uri = String(item.uri ?? "").toLowerCase();
  const leafBoost = (item["level"] === 2 || uri.endsWith(".md")) ? LEAF_BOOST : 0;
  const eventBoost =
    profile.wantsTemporal && (cat === "events" || uri.includes("/events/")) ? EVENT_BOOST : 0;
  const prefBoost =
    profile.wantsPreference && (cat === "preferences" || uri.includes("/preferences/"))
      ? PREF_BOOST
      : 0;
  const overlapBoost = lexicalOverlapBoost(
    profile.tokens,
    `${item.uri} ${abstract}`,
  );
  return base + leafBoost + eventBoost + prefBoost + overlapBoost;
}

/**
 * Drop near-duplicate hits. Events/cases dedupe by URI (different URIs
 * are treated as distinct events even when their abstracts collide);
 * everything else dedupes by abstract content with URI as fallback.
 * Stable in input order — first occurrence wins.
 */
export function dedupeItems<T extends RecallHit>(items: T[]): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const item of items) {
    const abstract = String(item.abstract ?? item["overview"] ?? "").trim().toLowerCase();
    const key = isEventOrCaseItem(item)
      ? `uri:${item.uri}`
      : (abstract || `uri:${item.uri}`);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Pipeline
// ---------------------------------------------------------------------------

/**
 * Filter → sort (desc by rank) → dedupe → truncate.
 *
 * Stable on tied rank scores: V8's Array.prototype.sort is stable since
 * ES2019, and we rely on that — items with identical rankItem outputs
 * come out in their input order.
 */
export function rankRecallHits<T extends RecallHit>(
  hits: T[],
  opts: RankRecallOptions,
): T[] {
  const profile = buildQueryProfile(opts.query);
  const filtered = hits.filter((it) => clampScore(it.score) >= opts.scoreThreshold);
  // Sort by rankItem desc. The sort is stable, so ties preserve input order.
  const sorted = filtered
    .slice()
    .sort((a, b) => rankItem(b, profile) - rankItem(a, profile));
  const deduped = dedupeItems(sorted);
  return deduped.slice(0, Math.max(0, Math.floor(opts.recallLimit)));
}
