#!/usr/bin/env node

/**
 * Auto-Recall Hook Script for Claude Code (UserPromptSubmit).
 *
 * Searches OpenViking for relevant context and injects an
 * <openviking-context> block. High-score items within the token budget
 * include resolved content; remaining items degrade to URI + score.
 *
 * Ranking: ported from openclaw-plugin/memory-ranking.ts (query profile
 * + boosts). Content resolution + budget: ported from
 * openclaw-plugin/index.ts resolveMemoryContent / buildMemoryLinesWithBudget,
 * modified so items beyond the budget are degraded (URI-only) instead of
 * dropped.
 */

import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { isBypassed, makeFetchJSON } from "./lib/ov-session.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const cfg = loadConfig();
const { log, logError } = createLogger("auto-recall");
const fetchJSON = makeFetchJSON(cfg);

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function approve(msg) {
  const out = { decision: "approve" };
  if (msg) out.hookSpecificOutput = { hookEventName: "UserPromptSubmit", additionalContext: msg };
  output(out);
}

// ---------------------------------------------------------------------------
// Ranking (ported from openclaw-plugin/memory-ranking.ts)
// ---------------------------------------------------------------------------

function clampScore(v) {
  if (typeof v !== "number" || Number.isNaN(v)) return 0;
  return Math.max(0, Math.min(1, v));
}

const PREFERENCE_QUERY_RE = /prefer|preference|favorite|favourite|like|偏好|喜欢|爱好|更倾向/i;
const TEMPORAL_QUERY_RE = /when|what time|date|day|month|year|yesterday|today|tomorrow|last|next|什么时候|何时|哪天|几月|几年|昨天|今天|明天/i;
const QUERY_TOKEN_RE = /[a-z0-9一-龥]{2,}/gi;
const STOPWORDS = new Set([
  "what","when","where","which","who","whom","whose","why","how","did","does",
  "is","are","was","were","the","and","for","with","from","that","this","your","you",
]);

function buildQueryProfile(query) {
  const text = query.trim();
  const allTokens = text.toLowerCase().match(QUERY_TOKEN_RE) || [];
  const tokens = allTokens.filter(t => !STOPWORDS.has(t));
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

function rankItem(item, profile) {
  const base = clampScore(item.score);
  const abstract = (item.abstract || item.overview || "").trim();
  const cat = (item.category || "").toLowerCase();
  const uri = (item.uri || "").toLowerCase();
  const leafBoost = (item.level === 2 || uri.endsWith(".md")) ? 0.12 : 0;
  const eventBoost = profile.wantsTemporal && (cat === "events" || uri.includes("/events/")) ? 0.1 : 0;
  const prefBoost = profile.wantsPreference && (cat === "preferences" || uri.includes("/preferences/")) ? 0.08 : 0;
  const overlapBoost = lexicalOverlapBoost(profile.tokens, `${item.uri} ${abstract}`);
  return base + leafBoost + eventBoost + prefBoost + overlapBoost;
}

/**
 * events/cases specialization (ported from openclaw-plugin/memory-ranking.ts
 * isEventOrCaseMemory): dedupe by URI instead of abstract.
 */
function isEventOrCaseItem(item) {
  const cat = (item.category || "").toLowerCase();
  const uri = (item.uri || "").toLowerCase();
  return cat === "events" || cat === "cases" || uri.includes("/events/") || uri.includes("/cases/");
}

function dedupeItems(items) {
  const seen = new Set();
  const out = [];
  for (const item of items) {
    const key = isEventOrCaseItem(item)
      ? `uri:${item.uri}`
      : ((item.abstract || item.overview || "").trim().toLowerCase() || `uri:${item.uri}`);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}

// ---------------------------------------------------------------------------
// URI space resolution (mirrors memory-server.ts normalizeTargetUri)
// ---------------------------------------------------------------------------

const USER_RESERVED_DIRS = new Set(["memories"]);
const AGENT_RESERVED_DIRS = new Set(["memories", "skills", "instructions", "workspaces"]);
const _spaceCache = {};

async function resolveScopeSpace(scope) {
  if (_spaceCache[scope]) return _spaceCache[scope];

  let fallbackSpace = "default";
  const status = await fetchJSON("/api/v1/system/status");
  if (status.ok && typeof status.result?.user === "string" && status.result.user.trim()) {
    fallbackSpace = status.result.user.trim();
  }

  const reservedDirs = scope === "user" ? USER_RESERVED_DIRS : AGENT_RESERVED_DIRS;
  const lsRes = await fetchJSON(`/api/v1/fs/ls?uri=${encodeURIComponent(`viking://${scope}`)}&output=original`);
  if (lsRes.ok && Array.isArray(lsRes.result)) {
    const spaces = lsRes.result
      .filter(e => e?.isDir)
      .map(e => (typeof e.name === "string" ? e.name.trim() : ""))
      .filter(n => n && !n.startsWith(".") && !reservedDirs.has(n));
    if (spaces.length > 0) {
      if (spaces.includes(fallbackSpace)) { _spaceCache[scope] = fallbackSpace; return fallbackSpace; }
      if (scope === "user" && spaces.includes("default")) { _spaceCache[scope] = "default"; return "default"; }
      if (spaces.length === 1) { _spaceCache[scope] = spaces[0]; return spaces[0]; }
    }
  }
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

// ---------------------------------------------------------------------------
// Multi-source search (scoped sources only — resources excluded to prevent
// cross-namespace leakage; use MCP search(scope="resources") explicitly)
// ---------------------------------------------------------------------------

const SOURCES = [
  { type: "memory", uri: "viking://user/memories",  bucket: "memories" },
  { type: "memory", uri: "viking://agent/memories", bucket: "memories" },
  { type: "skill",  uri: "viking://agent/skills",   bucket: "skills"   },
];

async function searchOneSource(query, source, limit) {
  const resolvedUri = await resolveTargetUri(source.uri);
  const res = await fetchJSON("/api/v1/search/find", {
    method: "POST",
    body: JSON.stringify({ query, target_uri: resolvedUri, limit, score_threshold: 0 }),
  });
  if (!res.ok) return [];
  const items = res.result?.[source.bucket] || [];
  return items.map(item => ({ ...item, _sourceType: source.type }));
}

async function searchAllSources(query, perSourceLimit) {
  const results = await Promise.all(SOURCES.map(src => searchOneSource(query, src, perSourceLimit)));
  const all = results.flat();
  log("search_summary", {
    counts: SOURCES.map((src, i) => ({ type: src.type, uri: src.uri, count: results[i].length })),
    total: all.length,
  });
  return all;
}

// ---------------------------------------------------------------------------
// Content resolution + budget formatting
// Ported from openclaw-plugin/index.ts resolveMemoryContent (line 1822-1850)
// and buildMemoryLinesWithBudget (line 1878-1907).
// Key difference: items beyond token budget are degraded to URI+score
// instead of being dropped entirely.
// ---------------------------------------------------------------------------

/** chars/4 heuristic (openclaw-plugin/index.ts:1812) */
function estimateTokens(text) {
  return text ? Math.ceil(text.length / 4) : 0;
}

/**
 * Resolve display content for a single item.
 * Ported from openclaw-plugin/index.ts:1822 resolveMemoryContent.
 */
async function resolveItemContent(item) {
  let content;

  if (cfg.recallPreferAbstract && (item.abstract || item.overview || "").trim()) {
    content = (item.abstract || item.overview).trim();
  } else if (item.level === 2) {
    try {
      const res = await fetchJSON(`/api/v1/content/read?uri=${encodeURIComponent(item.uri)}`);
      const body = res.ok && typeof res.result === "string" ? res.result.trim() : "";
      content = body || (item.abstract || item.overview || "").trim() || item.uri;
    } catch {
      content = (item.abstract || item.overview || "").trim() || item.uri;
    }
  } else {
    content = (item.abstract || item.overview || "").trim() || item.uri;
  }

  if (content.length > cfg.recallMaxContentChars) {
    content = content.slice(0, cfg.recallMaxContentChars) + "...";
  }

  return content;
}

/**
 * Build the injection block with token budget.
 * Front items (within budget) get full content lines.
 * Remaining items (beyond budget) degrade to URI + score only.
 */
async function buildInjectionBlock(items) {
  if (items.length === 0) return null;

  let budgetRemaining = cfg.recallTokenBudget;
  const lines = [
    "<openviking-context>",
    "Relevant context from OpenViking. Use the read MCP tool to expand URIs.",
  ];
  let contentCount = 0;
  let hintCount = 0;

  for (const item of items) {
    const score = (clampScore(item.score) * 100).toFixed(0);
    const uriLine = `- [${item._sourceType} ${score}%] ${item.uri}`;

    if (budgetRemaining > 0) {
      const content = await resolveItemContent(item);
      const contentLine = `- [${item._sourceType} ${score}%] ${content}`;
      const lineTokens = estimateTokens(contentLine);

      // First item always included even if over budget (openclaw spec §6.2)
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

  lines.push("</openviking-context>");

  log("injection_built", {
    contentItems: contentCount,
    hintItems: hintCount,
    budgetUsed: cfg.recallTokenBudget - budgetRemaining,
    budgetTotal: cfg.recallTokenBudget,
  });

  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  if (!cfg.autoRecall) {
    log("skip", { reason: "autoRecall disabled" });
    approve();
    return;
  }

  let input;
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    input = JSON.parse(Buffer.concat(chunks).toString());
  } catch {
    log("skip", { reason: "invalid stdin" });
    approve();
    return;
  }

  const userPrompt = (input.prompt || "").trim();
  const sessionId = input.session_id;
  const cwd = input.cwd;
  log("start", {
    query: userPrompt.slice(0, 200),
    queryLength: userPrompt.length,
    config: {
      recallLimit: cfg.recallLimit,
      scoreThreshold: cfg.scoreThreshold,
      recallMaxContentChars: cfg.recallMaxContentChars,
      recallTokenBudget: cfg.recallTokenBudget,
    },
  });

  if (isBypassed(cfg, { sessionId, cwd })) {
    log("skip", { reason: "bypass_session_pattern" });
    approve();
    return;
  }

  if (!userPrompt || userPrompt.length < cfg.minQueryLength) {
    log("skip", { reason: "query too short or empty" });
    approve();
    return;
  }

  const health = await fetchJSON("/health");
  if (!health.ok) {
    logError("health_check", "server unreachable");
    approve();
    return;
  }

  const perSourceLimit = Math.max(cfg.recallLimit * 2, 8);
  const raw = await searchAllSources(userPrompt, perSourceLimit);
  if (raw.length === 0) {
    log("skip", { reason: "no results" });
    approve();
    return;
  }

  const profile = buildQueryProfile(userPrompt);
  const filtered = raw.filter(it => clampScore(it.score) >= cfg.scoreThreshold);
  filtered.sort((a, b) => rankItem(b, profile) - rankItem(a, profile));
  const deduped = dedupeItems(filtered);
  const picked = deduped.slice(0, cfg.recallLimit);
  log("picked", {
    rawCount: raw.length,
    filteredCount: filtered.length,
    dedupedCount: deduped.length,
    pickedCount: picked.length,
    items: picked.map(it => ({ type: it._sourceType, uri: it.uri, score: clampScore(it.score) })),
  });

  if (picked.length === 0) {
    approve();
    return;
  }

  const block = await buildInjectionBlock(picked);
  approve(block);
}

main().catch((err) => { logError("uncaught", err); approve(); });
