#!/usr/bin/env node

/**
 * Auto-Recall Hook Script for Codex.
 *
 * Triggered by UserPromptSubmit hook.
 * Reads `prompt` from stdin → searches OpenViking → returns recalled memories
 * via `hookSpecificOutput.additionalContext` so Codex injects them into the turn.
 *
 * Codex output schema (codex-rs/hooks/schema/generated/user-prompt-submit.command.output.schema.json):
 *   { hookSpecificOutput: { hookEventName: "UserPromptSubmit", additionalContext: "<text>" } }
 * — `decision: "approve"` is NOT a codex thing; only `decision: "block"` is. So a no-op
 * is just `{}`.
 */

import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("auto-recall");

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function emit(additionalContext) {
  if (!additionalContext) {
    output({});
    return;
  }
  output({
    hookSpecificOutput: {
      hookEventName: "UserPromptSubmit",
      additionalContext,
    },
  });
}

async function fetchJSON(path, init = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), cfg.timeoutMs);
  try {
    const headers = { "Content-Type": "application/json" };
    if (cfg.apiKey) {
      headers["Authorization"] = `Bearer ${cfg.apiKey}`;
      headers["X-API-Key"] = cfg.apiKey;
    }
    if (cfg.account) headers["X-OpenViking-Account"] = cfg.account;
    if (cfg.user) headers["X-OpenViking-User"] = cfg.user;
    if (cfg.agentId) headers["X-OpenViking-Agent"] = cfg.agentId;
    const res = await fetch(`${cfg.baseUrl}${path}`, { ...init, headers, signal: controller.signal });
    const body = await res.json().catch(() => null);
    if (!body) return null;
    if (!res.ok || body.status === "error") return null;
    return body.result ?? body;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// Ranking
// ---------------------------------------------------------------------------

function clampScore(v) {
  if (typeof v !== "number" || Number.isNaN(v)) return 0;
  return Math.max(0, Math.min(1, v));
}

const PREFERENCE_QUERY_RE = /prefer|preference|favorite|favourite|like|偏好|喜欢|爱好|更倾向/i;
const TEMPORAL_QUERY_RE = /when|what time|date|day|month|year|yesterday|today|tomorrow|last|next|什么时候|何时|哪天|几月|几年|昨天|今天|明天/i;
const QUERY_TOKEN_RE = /[a-z0-9一-龥]{2,}/gi;
const STOPWORDS = new Set([
  "what", "when", "where", "which", "who", "whom", "whose", "why", "how", "did", "does",
  "is", "are", "was", "were", "the", "and", "for", "with", "from", "that", "this", "your", "you",
]);

function buildQueryProfile(query) {
  const text = query.trim();
  const allTokens = text.toLowerCase().match(QUERY_TOKEN_RE) || [];
  const tokens = allTokens.filter((t) => !STOPWORDS.has(t));
  return {
    tokens,
    wantsPreference: PREFERENCE_QUERY_RE.test(text),
    wantsTemporal: TEMPORAL_QUERY_RE.test(text),
  };
}

function lexicalOverlapBoost(tokens, text) {
  if (tokens.length === 0 || !text) return 0;
  const haystack = ` ${text.toLowerCase()} `;
  let matched = 0;
  for (const token of tokens.slice(0, 8)) {
    if (haystack.includes(token)) matched += 1;
  }
  return Math.min(0.2, (matched / Math.min(tokens.length, 4)) * 0.2);
}

function getRankingBreakdown(item, profile) {
  const base = clampScore(item.score);
  const abstract = (item.abstract || item.overview || "").trim();
  const cat = (item.category || "").toLowerCase();
  const uri = item.uri.toLowerCase();
  const leafBoost = (item.level === 2 || uri.endsWith(".md")) ? 0.12 : 0;
  const eventBoost = profile.wantsTemporal && (cat === "events" || uri.includes("/events/")) ? 0.1 : 0;
  const prefBoost = profile.wantsPreference && (cat === "preferences" || uri.includes("/preferences/")) ? 0.08 : 0;
  const overlapBoost = lexicalOverlapBoost(profile.tokens, `${item.uri} ${abstract}`);
  return {
    baseScore: base,
    leafBoost,
    eventBoost,
    prefBoost,
    overlapBoost,
    finalScore: base + leafBoost + eventBoost + prefBoost + overlapBoost,
  };
}

function rankForInjection(item, profile) {
  return getRankingBreakdown(item, profile).finalScore;
}

function dedupeByAbstract(items) {
  const seen = new Set();
  return items.filter((item) => {
    const key = (item.abstract || item.overview || "").trim().toLowerCase() || item.uri;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function pickMemories(items, limit, queryText) {
  if (items.length === 0 || limit <= 0) return [];
  const profile = buildQueryProfile(queryText);
  const sorted = [...items].sort((a, b) => rankForInjection(b, profile) - rankForInjection(a, profile));
  const deduped = dedupeByAbstract(sorted);
  const leaves = deduped.filter((m) => m.level === 2 || m.uri.endsWith(".md"));
  if (leaves.length >= limit) return leaves.slice(0, limit);
  const picked = [...leaves];
  const used = new Set(picked.map((m) => m.uri));
  for (const item of deduped) {
    if (picked.length >= limit) break;
    if (used.has(item.uri)) continue;
    picked.push(item);
  }
  return picked;
}

function postProcess(items, limit, threshold) {
  const seen = new Set();
  const sorted = [...items].sort((a, b) => clampScore(b.score) - clampScore(a.score));
  const result = [];
  for (const item of sorted) {
    if (item.level !== 2) continue;
    if (clampScore(item.score) < threshold) continue;
    const cat = (item.category || "").toLowerCase() || "unknown";
    const abs = (item.abstract || item.overview || "").trim().toLowerCase();
    const key = abs ? `${cat}:${abs}` : `uri:${item.uri}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(item);
    if (result.length >= limit) break;
  }
  return result;
}

// ---------------------------------------------------------------------------
// URI space resolution (mirrors MCP normalizeTargetUri)
// ---------------------------------------------------------------------------

const USER_RESERVED_DIRS = new Set(["memories"]);
const AGENT_RESERVED_DIRS = new Set(["memories", "skills", "instructions", "workspaces"]);
const _spaceCache = {};

async function resolveScopeSpace(scope) {
  if (_spaceCache[scope]) return _spaceCache[scope];

  let fallbackSpace = "default";
  try {
    const status = await fetchJSON("/api/v1/system/status");
    if (status && typeof status.user === "string" && status.user.trim()) {
      fallbackSpace = status.user.trim();
    }
  } catch { /* fallback */ }

  const reservedDirs = scope === "user" ? USER_RESERVED_DIRS : AGENT_RESERVED_DIRS;
  try {
    const entries = await fetchJSON(`/api/v1/fs/ls?uri=${encodeURIComponent(`viking://${scope}`)}&output=original`);
    if (Array.isArray(entries)) {
      const spaces = entries
        .filter((e) => e?.isDir)
        .map((e) => (typeof e.name === "string" ? e.name.trim() : ""))
        .filter((n) => n && !n.startsWith(".") && !reservedDirs.has(n));
      if (spaces.length > 0) {
        if (spaces.includes(fallbackSpace)) { _spaceCache[scope] = fallbackSpace; return fallbackSpace; }
        if (scope === "user" && spaces.includes("default")) { _spaceCache[scope] = "default"; return "default"; }
        if (spaces.length === 1) { _spaceCache[scope] = spaces[0]; return spaces[0]; }
      }
    }
  } catch { /* fallback */ }

  _spaceCache[scope] = fallbackSpace;
  return fallbackSpace;
}

async function resolveTargetUri(targetUri) {
  const trimmed = targetUri.trim().replace(/\/+$/, "");
  const m = trimmed.match(/^viking:\/\/(user|agent)(?:\/(.*))?$/);
  if (!m) return trimmed;
  const scope = m[1];
  const rawRest = (m[2] ?? "").trim();
  if (!rawRest) return trimmed;
  const parts = rawRest.split("/").filter(Boolean);
  if (parts.length === 0) return trimmed;
  const reservedDirs = scope === "user" ? USER_RESERVED_DIRS : AGENT_RESERVED_DIRS;
  if (!reservedDirs.has(parts[0])) return trimmed;
  const space = await resolveScopeSpace(scope);
  return `viking://${scope}/${space}/${parts.join("/")}`;
}

async function searchScope(query, targetUri, limit, bucket = "memories") {
  const resolvedUri = await resolveTargetUri(targetUri);
  const result = await fetchJSON("/api/v1/search/find", {
    method: "POST",
    body: JSON.stringify({ query, target_uri: resolvedUri, limit, score_threshold: 0 }),
  });
  return result?.[bucket] || [];
}

async function searchAll(query, limit) {
  const [userMems, agentMems, agentSkills] = await Promise.all([
    searchScope(query, "viking://user/memories", limit),
    searchScope(query, "viking://agent/memories", limit),
    searchScope(query, "viking://agent/skills", limit, "skills"),
  ]);
  log("search_complete", { scope: "user", rawCount: userMems.length, topScores: userMems.slice(0, 3).map((m) => m.score) });
  log("search_complete", { scope: "agent", rawCount: agentMems.length, topScores: agentMems.slice(0, 3).map((m) => m.score) });
  log("search_complete", { scope: "skills", rawCount: agentSkills.length, topScores: agentSkills.slice(0, 3).map((m) => m.score) });
  const all = [...userMems, ...agentMems, ...agentSkills];
  const seen = new Set();
  return all.filter((m) => {
    if (seen.has(m.uri)) return false;
    seen.add(m.uri);
    return true;
  });
}

async function readMemoryContent(uri) {
  try {
    const result = await fetchJSON(`/api/v1/content/read?uri=${encodeURIComponent(uri)}`);
    if (result && typeof result === "string" && result.trim()) return result.trim();
  } catch { /* fallback */ }
  return null;
}

async function main() {
  if (!cfg.autoRecall) {
    log("skip", { stage: "init", reason: "autoRecall disabled" });
    emit();
    return;
  }

  let input;
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    input = JSON.parse(Buffer.concat(chunks).toString());
  } catch {
    log("skip", { stage: "stdin_parse", reason: "invalid input" });
    emit();
    return;
  }

  const userPrompt = (input.prompt || "").trim();
  log("start", {
    query: userPrompt.slice(0, 200),
    queryLength: userPrompt.length,
    config: { recallLimit: cfg.recallLimit, scoreThreshold: cfg.scoreThreshold },
  });

  if (!userPrompt || userPrompt.length < cfg.minQueryLength) {
    log("skip", { stage: "query_check", reason: "query too short or empty" });
    emit();
    return;
  }

  const health = await fetchJSON("/health");
  if (!health) {
    logError("health_check", "server unreachable or unhealthy");
    emit();
    return;
  }

  const candidateLimit = Math.max(cfg.recallLimit * 4, 20);
  const allMemories = await searchAll(userPrompt, candidateLimit);
  if (allMemories.length === 0) {
    log("skip", { stage: "search", reason: "no results" });
    emit();
    return;
  }

  const processed = postProcess(allMemories, candidateLimit, cfg.scoreThreshold);
  log("post_process", { beforeCount: allMemories.length, afterCount: processed.length });

  const profile = buildQueryProfile(userPrompt);
  const ranked = [...processed]
    .map((item) => ({ item, breakdown: getRankingBreakdown(item, profile) }))
    .sort((a, b) => b.breakdown.finalScore - a.breakdown.finalScore);

  if (cfg.logRankingDetails) {
    for (const entry of ranked) {
      log("ranking_detail", { uri: entry.item.uri, ...entry.breakdown });
    }
  } else {
    log("ranking_summary", {
      candidateCount: processed.length,
      topCandidates: ranked.slice(0, 5).map((entry) => ({ uri: entry.item.uri, finalScore: entry.breakdown.finalScore })),
    });
  }

  const memories = pickMemories(processed, cfg.recallLimit, userPrompt);
  if (memories.length === 0) {
    log("skip", { stage: "pick", reason: "no memories survived ranking" });
    emit();
    return;
  }

  log("picked", { pickedCount: memories.length, uris: memories.map((m) => m.uri) });

  const lines = await Promise.all(
    memories.map(async (item) => {
      if (item.level === 2) {
        const content = await readMemoryContent(item.uri);
        if (content) return `- [${item.category || "memory"}] ${content}`;
      }
      return `- [${item.category || "memory"}] ${(item.abstract || item.overview || item.uri).trim()}`;
    }),
  );

  const memoryContext =
    "<relevant-memories>\n" +
    "The following long-term memories from OpenViking may be relevant to this conversation:\n" +
    lines.join("\n") + "\n" +
    "</relevant-memories>";

  emit(memoryContext);
}

main().catch((err) => { logError("uncaught", err); emit(); });
