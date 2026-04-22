import type { MemoryOpenVikingConfig } from "./config.js";
import { sanitizeUserTextForCapture } from "./text-utils.js";

export type RecallTier = "none" | "fast" | "full";

export type RecallTierDecision = {
  tier: RecallTier;
  reason: string;
  effectiveText: string;
};

export type RecallCacheEntry<T> = {
  key: string;
  sessionKey: string;
  query: string;
  value: T;
  createdAt: number;
};

const FULL_RECALL_RE =
  /\b(?:remember|recall|memory|memories|previous|earlier|last time|we decided|decided|decision|preference|prefer|always|never|root cause|debug|investigate|why|plan|architecture|compare|recommend|should|tradeoff|what happened|how did|prior|history|context)\b/i;

const DIRECT_COMMAND_RE =
  /^(?:\/[a-z0-9_-]+|\$[a-z][a-z0-9_-]*)(?:\s|$)/i;

const PURE_REFERENCE_RE =
  /^(?:https?:\/\/\S+|viking:\/\/\S+|\/[^\s]+|~\/[^\s]+)(?:\s+(?:https?:\/\/\S+|viking:\/\/\S+|\/[^\s]+|~\/[^\s]+))*$/i;

const ACK_RE =
  /^(?:ok|okay|k|yes|y|yep|yeah|no|nope|thanks|thank you|ty|cool|great|nice|done|lgtm|sgtm|go|doit|do it|proceed)$/i;

const MECHANICAL_RE =
  /\b(?:serve|share|publish|url|link|paste|copy|send me|show me|edit|rewrite|shorten|draft|format|fix spelling|typo|rename)\b/i;

const SHORT_FOLLOWUP_RE =
  /^(?:continue|go on|more|expand|again|same|that|this|these|those|it|do that|use that|yup|sure|also|and this|me this|me these)\b/i;

function normalizeForDecision(queryText: string): string {
  const sanitized = sanitizeUserTextForCapture(queryText).trim();
  const userInputMatch = sanitized.match(/\bUser input:\s*([\s\S]+)$/i);
  const focused = userInputMatch?.[1]?.trim() || sanitized;
  return focused.replace(/\s+/g, " ").trim();
}

export function normalizeRecallCacheQuery(queryText: string): string {
  return normalizeForDecision(queryText)
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 500);
}

export function makeRecallConfigVersion(
  cfg: Required<MemoryOpenVikingConfig>,
): string {
  return [
    cfg.recallLimit,
    cfg.recallScoreThreshold,
    cfg.recallMaxContentChars,
    cfg.recallPreferAbstract ? "abstract" : "full",
    cfg.recallTokenBudget,
  ].join(":");
}

export function makeRecallCacheKey(params: {
  queryText: string;
  agentId: string;
  cfg: Required<MemoryOpenVikingConfig>;
}): string {
  return [
    params.agentId,
    "user+agent",
    makeRecallConfigVersion(params.cfg),
    normalizeRecallCacheQuery(params.queryText),
  ].join("\u0000");
}

export function isFreshRecallCacheEntry<T>(
  entry: RecallCacheEntry<T> | undefined,
  ttlMs: number,
  now = Date.now(),
): entry is RecallCacheEntry<T> {
  return !!entry && ttlMs > 0 && now - entry.createdAt <= ttlMs;
}

function matchesOverride(patterns: string[], text: string): boolean {
  const lower = text.toLowerCase();
  return patterns.some((pattern) => {
    const trimmed = pattern.trim().toLowerCase();
    return !!trimmed && lower.includes(trimmed);
  });
}

export function decideRecallTier(params: {
  queryText: string;
  cfg: Required<MemoryOpenVikingConfig>;
  hasRecentCache: boolean;
}): RecallTierDecision {
  const effectiveText = normalizeForDecision(params.queryText);
  const compact = effectiveText.replace(/\s+/g, " ").trim();

  if (!params.cfg.autoRecall) {
    return { tier: "none", reason: "auto_recall_disabled", effectiveText: compact };
  }
  if (!params.cfg.adaptiveRecall) {
    return { tier: "full", reason: "adaptive_recall_disabled", effectiveText: compact };
  }
  if (compact.length < 5) {
    return { tier: "none", reason: "too_short", effectiveText: compact };
  }
  if (matchesOverride(params.cfg.recallTierOverrides.full ?? [], compact)) {
    return { tier: "full", reason: "full_override", effectiveText: compact };
  }
  if (matchesOverride(params.cfg.recallTierOverrides.none ?? [], compact)) {
    return { tier: "none", reason: "none_override", effectiveText: compact };
  }
  if (FULL_RECALL_RE.test(compact)) {
    return { tier: "full", reason: "memory_intent", effectiveText: compact };
  }
  if (ACK_RE.test(compact)) {
    return { tier: "none", reason: "acknowledgement", effectiveText: compact };
  }
  if (DIRECT_COMMAND_RE.test(compact)) {
    return { tier: "none", reason: "direct_command", effectiveText: compact };
  }
  if (PURE_REFERENCE_RE.test(compact)) {
    return { tier: "none", reason: "pure_reference", effectiveText: compact };
  }
  if (SHORT_FOLLOWUP_RE.test(compact) && compact.length <= 120) {
    return {
      tier: "fast",
      reason: params.hasRecentCache ? "short_followup_cached" : "short_followup_refresh",
      effectiveText: compact,
    };
  }
  if (MECHANICAL_RE.test(compact) && compact.length <= 220) {
    return { tier: "none", reason: "mechanical", effectiveText: compact };
  }
  return { tier: "full", reason: "substantive_default", effectiveText: compact };
}
