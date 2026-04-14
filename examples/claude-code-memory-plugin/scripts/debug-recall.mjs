#!/usr/bin/env node

/**
 * Debug Recall Pipeline — standalone test script.
 *
 * Usage:  node scripts/debug-recall.mjs "我喜欢什么语言"
 *
 * Walks through every stage of the auto-recall pipeline and prints
 * human-readable output so you can see exactly what OpenViking returns,
 * how scoring/ranking works, and what the final injected message looks like.
 */

import { loadConfig } from "./config.mjs";

// ---------------------------------------------------------------------------
// ANSI helpers
// ---------------------------------------------------------------------------

const C = {
  reset: "\x1b[0m",
  dim: "\x1b[2m",
  bold: "\x1b[1m",
  red: "\x1b[31m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  cyan: "\x1b[36m",
};

function header(title) {
  console.log(`\n${C.cyan}${C.bold}═══ ${title} ═══${C.reset}\n`);
}

function ok(msg) { console.log(`${C.green}${msg}${C.reset}`); }
function warn(msg) { console.log(`${C.yellow}${msg}${C.reset}`); }
function fail(msg) { console.log(`${C.red}${msg}${C.reset}`); }
function dim(msg) { console.log(`${C.dim}${msg}${C.reset}`); }

// ---------------------------------------------------------------------------
// CLI validation
// ---------------------------------------------------------------------------

const query = process.argv[2];
if (!query || !query.trim()) {
  fail("Usage: node scripts/debug-recall.mjs \"<query>\"");
  fail("  e.g. node scripts/debug-recall.mjs \"我喜欢什么语言\"");
  process.exit(1);
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

let cfg;
try {
  cfg = loadConfig();
} catch (err) {
  fail(`Config loading failed: ${err?.message || err}`);
  process.exit(1);
}

// ---------------------------------------------------------------------------
// fetchJSON (verbose version — shows response details on error)
// ---------------------------------------------------------------------------

async function fetchJSON(path, init = {}) {
  const url = `${cfg.baseUrl}${path}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), cfg.timeoutMs);
  try {
    const headers = { "Content-Type": "application/json" };
    if (cfg.apiKey) headers["X-API-Key"] = cfg.apiKey;
    if (cfg.agentId) headers["X-OpenViking-Agent"] = cfg.agentId;
    if (cfg.account) headers["X-OpenViking-Account"] = cfg.account;
    if (cfg.user) headers["X-OpenViking-User"] = cfg.user;
    const res = await fetch(url, { ...init, headers, signal: controller.signal });
    const body = await res.json();
    if (!res.ok || body.status === "error") {
      warn(`  HTTP ${res.status} from ${path}`);
      dim(`  Response: ${JSON.stringify(body).slice(0, 500)}`);
      return null;
    }
    return body.result ?? body;
  } catch (err) {
    if (err?.name === "AbortError") {
      fail(`  Request timed out after ${cfg.timeoutMs}ms: ${path}`);
    } else {
      fail(`  Fetch error for ${path}: ${err?.message || err}`);
    }
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// Pure functions — copied from auto-recall.mjs
// ---------------------------------------------------------------------------

function clampScore(v) {
  if (typeof v !== "number" || Number.isNaN(v)) return 0;
  return Math.max(0, Math.min(1, v));
}

const PREFERENCE_QUERY_RE = /prefer|preference|favorite|favourite|like|偏好|喜欢|爱好|更倾向/i;
const TEMPORAL_QUERY_RE = /when|what time|date|day|month|year|yesterday|today|tomorrow|last|next|什么时候|何时|哪天|几月|几年|昨天|今天|明天/i;
const QUERY_TOKEN_RE = /[a-z0-9\u4e00-\u9fa5]{2,}/gi;
const STOPWORDS = new Set([
  "what","when","where","which","who","whom","whose","why","how","did","does",
  "is","are","was","were","the","and","for","with","from","that","this","your","you",
]);

function buildQueryProfile(queryText) {
  const text = queryText.trim();
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

function rankForInjection(item, profile) {
  const base = clampScore(item.score);
  const abstract = (item.abstract || item.overview || "").trim();
  const cat = (item.category || "").toLowerCase();
  const uri = item.uri.toLowerCase();
  const leafBoost = (item.level === 2 || uri.endsWith(".md")) ? 0.12 : 0;
  const eventBoost = profile.wantsTemporal && (cat === "events" || uri.includes("/events/")) ? 0.1 : 0;
  const prefBoost = profile.wantsPreference && (cat === "preferences" || uri.includes("/preferences/")) ? 0.08 : 0;
  const overlapBoost = lexicalOverlapBoost(profile.tokens, `${item.uri} ${abstract}`);
  return base + leafBoost + eventBoost + prefBoost + overlapBoost;
}

function dedupeByAbstract(items) {
  const seen = new Set();
  return items.filter(item => {
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
  const leaves = deduped.filter(m => m.level === 2 || m.uri.endsWith(".md"));
  if (leaves.length >= limit) return leaves.slice(0, limit);
  const picked = [...leaves];
  const used = new Set(picked.map(m => m.uri));
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
// URI space resolution — copied from auto-recall.mjs
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
  } catch { /* use fallback */ }

  const reservedDirs = scope === "user" ? USER_RESERVED_DIRS : AGENT_RESERVED_DIRS;
  try {
    const entries = await fetchJSON(`/api/v1/fs/ls?uri=${encodeURIComponent(`viking://${scope}`)}&output=original`);
    if (Array.isArray(entries)) {
      const spaces = entries
        .filter(e => e?.isDir)
        .map(e => (typeof e.name === "string" ? e.name.trim() : ""))
        .filter(n => n && !n.startsWith(".") && !reservedDirs.has(n));
      if (spaces.length > 0) {
        if (spaces.includes(fallbackSpace)) { _spaceCache[scope] = fallbackSpace; return fallbackSpace; }
        if (scope === "user" && spaces.includes("default")) { _spaceCache[scope] = "default"; return "default"; }
        if (spaces.length === 1) { _spaceCache[scope] = spaces[0]; return spaces[0]; }
      }
    }
  } catch { /* use fallback */ }

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
// Search — adapted from auto-recall.mjs (verbose on error)
// ---------------------------------------------------------------------------

async function searchScope(queryText, targetUri, limit) {
  const resolvedUri = await resolveTargetUri(targetUri);
  dim(`  target: ${targetUri} -> ${resolvedUri}`);
  const result = await fetchJSON("/api/v1/search/find", {
    method: "POST",
    body: JSON.stringify({ query: queryText, target_uri: resolvedUri, limit, score_threshold: 0 }),
  });
  return result?.memories || [];
}

async function searchBothScopes(queryText, limit) {
  console.log(`${C.dim}Searching user scope...${C.reset}`);
  const userMems = await searchScope(queryText, "viking://user/memories", limit);
  console.log(`${C.dim}Searching agent scope...${C.reset}`);
  const agentMems = await searchScope(queryText, "viking://agent/memories", limit);

  const all = [...userMems, ...agentMems];
  const uriSet = new Set();
  return {
    userMems,
    agentMems,
    combined: all.filter(m => {
      if (uriSet.has(m.uri)) return false;
      uriSet.add(m.uri);
      return true;
    }),
  };
}

async function readMemoryContent(uri) {
  try {
    const result = await fetchJSON(`/api/v1/content/read?uri=${encodeURIComponent(uri)}`);
    if (result && typeof result === "string" && result.trim()) return result.trim();
  } catch (err) {
    warn(`  Failed to read content for ${uri}: ${err?.message || err}`);
  }
  return null;
}

// ---------------------------------------------------------------------------
// Print helpers
// ---------------------------------------------------------------------------

function printSearchResults(label, items) {
  console.log(`${C.bold}${label}${C.reset} (${items.length} result${items.length === 1 ? "" : "s"}):`);
  if (items.length === 0) {
    dim("  (none)");
    return;
  }
  for (const item of items) {
    const score = clampScore(item.score).toFixed(4);
    console.log(`  ${C.bold}${item.uri}${C.reset}`);
    dim(`    score=${score}  level=${item.level}  category=${item.category || "(none)"}`);
    dim(`    abstract: ${(item.abstract || item.overview || "(none)").trim().slice(0, 120)}`);
  }
}

// ---------------------------------------------------------------------------
// Main pipeline
// ---------------------------------------------------------------------------

async function main() {
  // ── Stage 1: Config ──
  header("Config Summary");
  console.log(`  mode:           ${cfg.mode}`);
  console.log(`  baseUrl:        ${C.bold}${cfg.baseUrl}${C.reset}`);
  console.log(`  recallLimit:    ${cfg.recallLimit}`);
  console.log(`  scoreThreshold: ${cfg.scoreThreshold}`);
  console.log(`  timeoutMs:      ${cfg.timeoutMs}`);
  console.log(`  agentId:        ${cfg.agentId}`);
  console.log(`  debug:          ${cfg.debug}`);
  console.log(`  clientConfig:   ${C.dim}${cfg.configPath}${C.reset}`);
  if (cfg.mode === "local") {
    console.log(`  serverConfig:   ${C.dim}${cfg.serverConfigPath}${C.reset}`);
    if (cfg.serverConfigError) {
      warn(`  local ov.conf fallback unavailable: ${cfg.serverConfigError}`);
    }
  }
  console.log(`\n  Query: ${C.bold}${query}${C.reset}`);

  // Query profile
  const profile = buildQueryProfile(query);
  dim(`  tokens:          [${profile.tokens.join(", ")}]`);
  dim(`  wantsPreference: ${profile.wantsPreference}`);
  dim(`  wantsTemporal:   ${profile.wantsTemporal}`);

  // ── Stage 2: Health check ──
  header("Health Check");
  const health = await fetchJSON("/health");
  if (health) {
    ok(`  OK`);
    dim(`  ${JSON.stringify(health).slice(0, 300)}`);
  } else {
    fail(`  FAILED — OpenViking is not reachable at ${cfg.baseUrl}`);
    fail("  The search stages below will likely fail as well.");
  }

  // ── Stage 3: Raw search results ──
  header("Raw Search Results");
  const candidateLimit = Math.max(cfg.recallLimit * 4, 20);
  dim(`  candidateLimit: ${candidateLimit}`);

  const { userMems, agentMems, combined } = await searchBothScopes(query, candidateLimit);

  console.log();
  printSearchResults("User scope (viking://user/memories)", userMems);
  console.log();
  printSearchResults("Agent scope (viking://agent/memories)", agentMems);
  console.log();
  dim(`  Combined (deduplicated): ${combined.length} result${combined.length === 1 ? "" : "s"}`);

  if (combined.length === 0) {
    warn("\n  No search results found. Pipeline ends here.");
    return;
  }

  // ── Stage 4: PostProcess ──
  header("PostProcess Results");
  const processed = postProcess(combined, candidateLimit, cfg.scoreThreshold);
  console.log(`  Raw count:      ${combined.length}`);
  console.log(`  Filtered count: ${processed.length}`);
  const dropped = combined.length - processed.length;
  if (dropped > 0) {
    dim(`  Dropped ${dropped} item${dropped === 1 ? "" : "s"} (non-leaf, below threshold, or duplicate)`);
  }

  if (processed.length === 0) {
    warn("\n  All candidates were filtered out. Pipeline ends here.");
    return;
  }

  // ── Stage 5: Ranking details ──
  header("Ranking Details");
  const rankedItems = [...processed].sort((a, b) => rankForInjection(b, profile) - rankForInjection(a, profile));

  for (const item of rankedItems) {
    const base = clampScore(item.score);
    const abstract = (item.abstract || item.overview || "").trim();
    const cat = (item.category || "").toLowerCase();
    const uri = item.uri.toLowerCase();

    const leafBoost = (item.level === 2 || uri.endsWith(".md")) ? 0.12 : 0;
    const eventBoost = profile.wantsTemporal && (cat === "events" || uri.includes("/events/")) ? 0.1 : 0;
    const prefBoost = profile.wantsPreference && (cat === "preferences" || uri.includes("/preferences/")) ? 0.08 : 0;
    const overlapBoost = lexicalOverlapBoost(profile.tokens, `${item.uri} ${abstract}`);
    const finalScore = base + leafBoost + eventBoost + prefBoost + overlapBoost;

    console.log(`  ${C.bold}${item.uri}${C.reset}`);
    dim(`    baseScore:    ${base.toFixed(4)}`);
    dim(`    leafBoost:    ${leafBoost > 0 ? C.reset + C.green + "+" + leafBoost.toFixed(4) + C.reset + C.dim : "+0.0000"}`);
    dim(`    eventBoost:   ${eventBoost > 0 ? C.reset + C.green + "+" + eventBoost.toFixed(4) + C.reset + C.dim : "+0.0000"}`);
    dim(`    prefBoost:    ${prefBoost > 0 ? C.reset + C.green + "+" + prefBoost.toFixed(4) + C.reset + C.dim : "+0.0000"}`);
    dim(`    overlapBoost: ${overlapBoost > 0 ? C.reset + C.green + "+" + overlapBoost.toFixed(4) + C.reset + C.dim : "+0.0000"}`);
    console.log(`    ${C.bold}finalScore:   ${finalScore.toFixed(4)}${C.reset}`);
  }

  // ── Stage 6: Final picks ──
  header("Final Picks");
  const memories = pickMemories(processed, cfg.recallLimit, query);

  if (memories.length === 0) {
    warn("  No memories selected after pick stage.");
    return;
  }

  console.log(`  Selected ${C.bold}${memories.length}${C.reset} of ${processed.length} candidates (limit: ${cfg.recallLimit})\n`);

  const lines = [];
  for (const item of memories) {
    console.log(`  ${C.green}*${C.reset} ${C.bold}${item.uri}${C.reset}`);
    dim(`    score=${clampScore(item.score).toFixed(4)}  level=${item.level}  category=${item.category || "(none)"}`);

    let lineText;
    if (item.level === 2) {
      const content = await readMemoryContent(item.uri);
      if (content) {
        ok(`    content: ${content.slice(0, 200)}${content.length > 200 ? "..." : ""}`);
        lineText = `- [${item.category || "memory"}] ${content}`;
      } else {
        warn("    content: (could not read)");
        lineText = `- [${item.category || "memory"}] ${(item.abstract || item.overview || item.uri).trim()}`;
      }
    } else {
      lineText = `- [${item.category || "memory"}] ${(item.abstract || item.overview || item.uri).trim()}`;
      dim(`    abstract: ${(item.abstract || item.overview || "(none)").trim().slice(0, 200)}`);
    }
    lines.push(lineText);
  }

  // ── Stage 7: Generated system message ──
  header("Generated System Message");
  const memoryContext =
    "<relevant-memories>\n" +
    "The following long-term memories from OpenViking may be relevant to this conversation:\n" +
    lines.join("\n") + "\n" +
    "</relevant-memories>";

  console.log(memoryContext);
  console.log();
  dim(`  (${Buffer.byteLength(memoryContext, "utf-8")} bytes)`);
}

main().catch((err) => {
  fail(`\nUnexpected error: ${err?.message || err}`);
  if (err?.stack) dim(err.stack);
  process.exit(1);
});
