// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

import { compressRecallContext } from "./recall-compress-core.mjs";

const PREFERENCE_QUERY_RE = /prefer|preference|favorite|favourite|like|偏好|喜欢|爱好|更倾向/i;
const TEMPORAL_QUERY_RE = /when|what time|date|day|month|year|yesterday|today|tomorrow|last|next|什么时候|何时|哪天|几月|几年|昨天|今天|明天/i;
const QUERY_TOKEN_RE = /[a-z0-9一-龥]{2,}/gi;
const STOPWORDS = new Set([
  "what", "when", "where", "which", "who", "whom", "whose", "why", "how", "did", "does",
  "is", "are", "was", "were", "the", "and", "for", "with", "from", "that", "this", "your", "you",
]);
const USER_RESERVED_DIRS = new Set(["memories", "skills"]);
const SOURCES = [
  { type: "memory", uri: "viking://user/memories", bucket: "memories" },
  { type: "skill", uri: "viking://user/skills", bucket: "skills" },
];
const DEFAULT_CONTEXT_LIMIT = 10;
const DEFAULT_CONTEXT_MAX_TOKENS = 1600;
const DEFAULT_REWRITE_MAX_BULLETS = 6;
const CODING_QUOTA_WEIGHTS = {
  events: 1,
  entities: 2,
  preferences: 1,
  experiences: 1,
  resources: 3,
  skills: 2,
};

let userSpaceCache = "";

export function estimateTokens(text) {
  return text ? Math.ceil(String(text).length / 4) : 0;
}

function scaleQuotas(limit, weights) {
  const slots = Math.max(1, Math.floor(Number(limit) || DEFAULT_CONTEXT_LIMIT));
  const order = Object.keys(weights);
  const quotas = Object.fromEntries(order.map((key) => [key, 0]));
  if (slots < order.length) {
    for (const key of order) quotas[key] = 1;
    return quotas;
  }

  for (const key of order) quotas[key] = 1;
  const totalWeight = Object.values(weights).reduce((sum, weight) => sum + weight, 0);
  const ideals = Object.fromEntries(
    order.map((key) => [key, slots * weights[key] / totalWeight]),
  );
  while (order.reduce((sum, key) => sum + quotas[key], 0) < slots) {
    const key = order.reduce((best, candidate) => (
      ideals[candidate] - quotas[candidate] > ideals[best] - quotas[best]
        ? candidate
        : best
    ));
    quotas[key] += 1;
  }
  return quotas;
}

function legacyMemoryQuotas(limit) {
  return {
    ...scaleQuotas(limit, { events: 10, entities: 10, preferences: 3 }),
    experiences: 0,
  };
}

function codingQuotas(limit) {
  return scaleQuotas(limit, CODING_QUOTA_WEIGHTS);
}

export function buildRecallEndpointBody(cfg = {}) {
  const limit = Math.max(Number(cfg.recallLimit || DEFAULT_CONTEXT_LIMIT), 1);
  const body = {
    query: "",
    quotas: legacyMemoryQuotas(limit),
    max_chars: Math.max(Number(cfg.recallMaxContentChars || 0) * limit, 1000),
    min_score: Number.isFinite(Number(cfg.scoreThreshold)) ? Number(cfg.scoreThreshold) : 0.35,
    render: true,
  };
  if (cfg.recallPeerScope === "actor") body.peer_scope = "actor";
  return body;
}

/**
 * Body for the server-side context face. The plugin declares intent (coding
 * purpose, budget, session) and leaves the mechanics — quota ratios, tier
 * degradation, cross-turn dedup — to the server's defaults.
 */
export function buildContextSearchBody(cfg = {}, options = {}) {
  const rewriteMode = String(cfg.recallRewrite || "off").toLowerCase();
  const limit = Math.max(1, Math.floor(Number(cfg.recallLimit || DEFAULT_CONTEXT_LIMIT)));
  const maxTokens = Math.max(
    64,
    Math.floor(Number(cfg.recallMaxTokens || DEFAULT_CONTEXT_MAX_TOKENS)),
  );
  const body = {
    query: "",
    mode: "context",
    purpose: "coding",
    score_threshold: Number.isFinite(Number(cfg.scoreThreshold)) ? Number(cfg.scoreThreshold) : 0.35,
  };
  const limitConfigured = cfg.recallLimitConfigured === true;
  const maxTokensConfigured = cfg.recallMaxTokensConfigured === true;
  if (limitConfigured) body.quotas = codingQuotas(limit);
  if (maxTokensConfigured) body.max_tokens = maxTokens;
  if (cfg.recallPeerScope === "actor") body.peer_scope = "actor";

  const sessionId = String(options.sessionId || "").trim();
  if (sessionId) {
    body.session_id = sessionId;
    const queryExpansionConfigured = cfg.recallQueryExpansionConfigured === true;
    if (queryExpansionConfigured) {
      body.query_expansion = cfg.recallQueryExpansion === "off" ? "off" : "auto";
    }
    const dedupTurns = Number(cfg.recallDedupTurns);
    const resolvedDedupTurns = Number.isFinite(dedupTurns)
      ? Math.max(0, Math.floor(dedupTurns))
      : 5;
    if (resolvedDedupTurns > 0) body.dedup_turns = resolvedDedupTurns;
  }

  const excludeUris = Array.isArray(options.excludeUris) ? options.excludeUris.slice(0, 200) : [];
  if (excludeUris.length) body.exclude_uris = excludeUris;

  if (rewriteMode === "server") body.rewrite = true;
  else if (rewriteMode === "auto" && !options.localCompressorAvailable) body.rewrite = "auto";
  const rewriteMaxBullets = Math.max(
    1,
    Math.floor(Number(cfg.recallCompressMaxBullets || DEFAULT_REWRITE_MAX_BULLETS)),
  );
  const rewriteMaxBulletsConfigured = cfg.recallCompressMaxBulletsConfigured === true;
  if (body.rewrite !== undefined && rewriteMaxBulletsConfigured) {
    body.rewrite_max_bullets = rewriteMaxBullets;
  }
  return body;
}

// Server default for retrieval.recall_rewrite_timeout_s plus room for the
// retrieval that precedes it, still well inside the 60s prompt-hook budget.
const SERVER_REWRITE_REQUEST_TIMEOUT_MS = 35000;

/**
 * HTTP deadline for one context request, or undefined to keep the caller's own.
 *
 * The ordinary request timeout is shorter than the server's rewrite fuse, so a
 * digest that finishes inside its own fuse would be aborted client-side. That
 * loses the whole response rather than just the digest, including the
 * uncompressed block the server still returns when a rewrite fails.
 */
export function contextRequestTimeoutMs(cfg = {}, serverRewrite = false) {
  if (!serverRewrite) return undefined;
  const configured = Number(cfg.recallContextTimeoutMs);
  if (Number.isFinite(configured) && configured > 0) return Math.max(1000, Math.floor(configured));
  return Math.max(Number(cfg.timeoutMs) || 0, SERVER_REWRITE_REQUEST_TIMEOUT_MS);
}

/**
 * Strip the context-face fields a pre-context server rejects, converting the
 * token budget back to v1's character budget.
 */
export function downgradeToRecallBody(contextBody = {}, cfg = {}) {
  const body = buildRecallEndpointBody(cfg);
  body.query = contextBody.query || "";
  body.max_chars = Math.max(1000, Math.floor(Number(contextBody.max_tokens || 1600) * 4));
  if (contextBody.peer_scope) body.peer_scope = contextBody.peer_scope;
  return body;
}

function clampScore(v) {
  if (typeof v !== "number" || Number.isNaN(v)) return 0;
  return Math.max(0, Math.min(1, v));
}

function buildQueryProfile(query) {
  const text = query.trim();
  const allTokens = text.toLowerCase().match(QUERY_TOKEN_RE) || [];
  return {
    tokens: allTokens.filter((t) => !STOPWORDS.has(t)),
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

async function resolveUserSpace(fetchJSON, actorPeerId = "") {
  if (userSpaceCache) return userSpaceCache;

  let fallbackSpace = "default";
  const status = await fetchJSON("/api/v1/system/status");
  if (status.ok && typeof status.result?.user === "string" && status.result.user.trim()) {
    fallbackSpace = status.result.user.trim();
  }

  const lsRes = await fetchJSON(
    `/api/v1/fs/ls?uri=${encodeURIComponent("viking://user")}&output=original`,
    {},
    { actorPeerId },
  );
  if (lsRes.ok && Array.isArray(lsRes.result)) {
    const spaces = lsRes.result
      .filter((e) => e?.isDir)
      .map((e) => (typeof e.name === "string" ? e.name.trim() : ""))
      .filter((n) => n && !n.startsWith(".") && !USER_RESERVED_DIRS.has(n));
    if (spaces.length > 0) {
      if (spaces.includes(fallbackSpace)) { userSpaceCache = fallbackSpace; return fallbackSpace; }
      if (spaces.includes("default")) { userSpaceCache = "default"; return "default"; }
      if (spaces.length === 1) { userSpaceCache = spaces[0]; return spaces[0]; }
    }
  }
  userSpaceCache = fallbackSpace;
  return fallbackSpace;
}

async function resolveTargetUri(fetchJSON, targetUri, actorPeerId = "") {
  const trimmed = targetUri.trim().replace(/\/+$/, "");
  const m = trimmed.match(/^viking:\/\/user(?:\/(.*))?$/);
  if (!m) return trimmed;
  const rawRest = (m[1] ?? "").trim();
  if (!rawRest) return trimmed;
  const parts = rawRest.split("/").filter(Boolean);
  if (parts.length === 0) return trimmed;
  if (!USER_RESERVED_DIRS.has(parts[0])) return trimmed;
  const space = await resolveUserSpace(fetchJSON, actorPeerId);
  return `viking://user/${space}/${parts.join("/")}`;
}

async function searchOneSource(fetchJSON, query, source, limit, actorPeerId = "") {
  const resolvedUri = await resolveTargetUri(fetchJSON, source.uri, actorPeerId);
  const body = { query, target_uri: resolvedUri, limit, score_threshold: 0 };
  const res = await fetchJSON("/api/v1/search/find", {
    method: "POST",
    body: JSON.stringify(body),
  }, { actorPeerId });
  if (!res.ok) return [];
  const items = res.result?.[source.bucket] || [];
  return items.map((item) => ({ ...item, _sourceType: source.type }));
}

async function searchAllSources(fetchJSON, query, perSourceLimit, actorPeerId = "", log = () => {}) {
  const results = await Promise.all(
    SOURCES.map((src) => searchOneSource(fetchJSON, query, src, perSourceLimit, actorPeerId)),
  );
  const all = results.flat();
  log("recall_search_summary", {
    counts: SOURCES.map((src, i) => ({ type: src.type, uri: src.uri, count: results[i].length })),
    total: all.length,
  });
  return all;
}

async function resolveItemContent(fetchJSON, item, cfg, actorPeerId = "") {
  let content;

  if (cfg.recallPreferAbstract && (item.abstract || item.overview || "").trim()) {
    content = (item.abstract || item.overview).trim();
  } else if (item.level === 2) {
    try {
      const res = await fetchJSON(
        `/api/v1/content/read?uri=${encodeURIComponent(item.uri)}`,
        {},
        { actorPeerId },
      );
      const body = res.ok && typeof res.result === "string" ? res.result.trim() : "";
      content = body || (item.abstract || item.overview || "").trim() || item.uri;
    } catch {
      content = (item.abstract || item.overview || "").trim() || item.uri;
    }
  } else {
    content = (item.abstract || item.overview || "").trim() || item.uri;
  }

  const maxChars = Math.max(50, Number(cfg.recallMaxContentChars || 500));
  if (content.length > maxChars) content = `${content.slice(0, maxChars)}...`;
  return content;
}

async function buildFallbackInjectionBlock(fetchJSON, items, cfg, actorPeerId = "", log = () => {}) {
  if (items.length === 0) return null;

  let budgetRemaining = Math.max(200, Number(cfg.recallTokenBudget || 2000));
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
      const content = await resolveItemContent(fetchJSON, item, cfg, actorPeerId);
      const contentLine = `- [${item._sourceType} ${score}%] ${content}`;
      const lineTokens = estimateTokens(contentLine);

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

  const budgetUsed = Math.max(200, Number(cfg.recallTokenBudget || 2000)) - budgetRemaining;
  log("recall_injection_built", {
    contentItems: contentCount,
    hintItems: hintCount,
    budgetUsed,
    budgetTotal: Math.max(200, Number(cfg.recallTokenBudget || 2000)),
  });

  return lines.join("\n");
}

const LEGACY_CACHE_TTL_MS = 6 * 60 * 60 * 1000;

function stateFile(name) {
  const override = String(process.env.OPENVIKING_STATE_DIR || "").trim();
  return override ? join(override, name) : join(homedir(), ".openviking", "state", name);
}

async function readJsonFile(path) {
  try { return JSON.parse(await readFile(path, "utf8")); } catch { return null; }
}

async function writeJsonFile(path, value) {
  try {
    await mkdir(dirname(path), { recursive: true });
    const tmp = `${path}.tmp`;
    await writeFile(tmp, JSON.stringify(value));
    await rename(tmp, path);
  } catch { /* best effort */ }
}

/**
 * Hooks are one-shot processes, so "this server has no context face" has to be
 * remembered on disk or every turn pays for a rejected request.
 */
export async function isContextFaceLegacy(path = stateFile("context-face.json"), now = Date.now()) {
  const cached = await readJsonFile(path);
  return Boolean(cached?.legacyUntil && Number(cached.legacyUntil) > now);
}

export async function markContextFaceLegacy(path = stateFile("context-face.json"), now = Date.now()) {
  await writeJsonFile(path, { legacyUntil: now + LEGACY_CACHE_TTL_MS });
}

function looksLikeUnknownField(res) {
  const text = JSON.stringify(res?.error ?? res?.result ?? res?.detail ?? "").toLowerCase();
  return text.includes("extra") || text.includes("mode") || text.includes("unexpected");
}

function wrapContext(body) {
  return [
    "<openviking-context>",
    "Relevant memory from OpenViking. Use the recall/read MCP tools to expand URIs.",
    body,
    "</openviking-context>",
  ].join("\n");
}

/**
 * Server-assembled context: the context face when the deployment has it, else
 * the deprecated /recall preset. Returns the injection block, "" when there was
 * nothing relevant, or null when no server-side path was usable at all.
 */
export async function buildServerAssembledBlock(fetchJSON, cfg, query, options = {}) {
  const actorPeerId = options.actorPeerId ?? cfg.peerId ?? "";
  const log = options.log || (() => {});

  const block = await recallViaContextFace(fetchJSON, cfg, query, { ...options, actorPeerId }, log);
  if (block !== null) return block;
  return recallViaEndpoint(fetchJSON, cfg, query, actorPeerId, log);
}

/**
 * Raw server-assembled context, or null when the deployment has no context face.
 * Returns `{ rendered, entries, digest, stats }` — callers that need the entries
 * (their own compression, their own envelope) use this instead of the block
 * builders below.
 */
export async function fetchAssembledContext(fetchJSON, cfg, query, options = {}) {
  const actorPeerId = options.actorPeerId || "";
  const log = options.log || (() => {});
  if (await isContextFaceLegacy(options.legacyCachePath)) return null;

  const body = buildContextSearchBody(cfg, options);
  body.query = query;
  const res = await fetchJSON("/api/v1/search/search", {
    method: "POST",
    body: JSON.stringify(body),
  }, { actorPeerId, timeoutMs: contextRequestTimeoutMs(cfg, body.rewrite !== undefined) });

  if (!res.ok) {
    const status = res.status || 0;
    if ((status === 400 || status === 422) && looksLikeUnknownField(res)) {
      await markContextFaceLegacy(options.legacyCachePath);
      log("recall_context_face_unsupported", { status });
    } else {
      log("recall_context_face_error", { status });
    }
    return null;
  }

  const result = res.result || {};
  const stats = result.stats || {};
  log("recall_context_assembled", {
    entries: Array.isArray(result.entries) ? result.entries.length : 0,
    usedTokens: stats.used_tokens || 0,
    tiers: stats.tier_counts || {},
    rewrite: stats.rewrite || "off",
  });
  return {
    rendered: String(result.rendered || "").trim(),
    entries: Array.isArray(result.entries) ? result.entries : [],
    digest: String(result.digest || "").trim(),
    stats,
  };
}

/**
 * Entry field compatibility: the context face returns `category`/`text`, while
 * the deprecated /recall v1 shape used `type` plus `content`/`summary`.
 */
export function normalizeContextEntry(entry = {}) {
  return {
    uri: String(entry.uri || "").trim(),
    category: String(entry.category || entry.type || "memory").trim() || "memory",
    detail: String(entry.detail || entry.mode || "").trim(),
    score: Number(entry.score) || 0,
    text: String(
      entry.text || entry.content || entry.summary || entry.abstract || entry.uri || "",
    ).trim(),
  };
}

async function recallViaContextFace(fetchJSON, cfg, query, options, log) {
  const assembled = await fetchAssembledContext(fetchJSON, cfg, query, { ...options, log });
  if (assembled === null) return null;

  const { rendered, entries } = assembled;
  let digest = assembled.digest;
  const mode = String(cfg.recallRewrite || "off").toLowerCase();
  if (String(assembled.stats?.rewrite || "").toLowerCase() === "no_relevant") {
    log("recall_server_compression", { status: "empty" });
    return "";
  }
  const wantsLocal = mode === "client" || (mode === "auto" && !digest);
  if (wantsLocal && rendered && typeof options.runCompressor === "function") {
    try {
      const compression = await compressRecallContext({
        query,
        rendered,
        entries,
        cfg,
        runCompressor: options.runCompressor,
        cachePath: options.digestCachePath || stateFile("recall-digest.json"),
        now: Date.now(),
      });
      log("recall_local_compression", { status: compression.status });
      if (compression.status === "ok") digest = compression.context;
      if (compression.status === "empty") return "";
    } catch (err) {
      log("recall_local_compression_failed", { error: String(err?.message || err) });
    }
  }

  const injected = digest || rendered;
  if (!injected) return "";
  return wrapContext(injected);
}

async function recallViaEndpoint(fetchJSON, cfg, query, actorPeerId = "", log = () => {}) {
  const body = buildRecallEndpointBody(cfg);
  body.query = query;
  const res = await postRecall(fetchJSON, body, { actorPeerId, log });
  if (!res.ok) {
    log("recall_endpoint_fallback", { status: res.status || 0 });
    return null;
  }
  const rendered = String(res.result?.rendered || "").trim();
  if (!rendered) return "";
  return wrapContext(rendered);
}

export async function postRecall(fetchJSON, body, opts = {}) {
  const actorPeerId = opts.actorPeerId || "";
  const log = opts.log || (() => {});
  const request = { ...body };
  const res = await fetchJSON("/api/v1/search/recall", {
    method: "POST",
    body: JSON.stringify(request),
  }, { actorPeerId });
  if (!request.peer_scope || (res.status !== 400 && res.status !== 422)) {
    return res;
  }

  const downgraded = { ...request };
  delete downgraded.peer_scope;
  log("recall_peer_scope_downgrade", { status: res.status || 0 });
  return fetchJSON("/api/v1/search/recall", {
    method: "POST",
    body: JSON.stringify(downgraded),
  }, { actorPeerId });
}

export async function buildRecallBlock(fetchJSON, cfg, query, options = {}) {
  const actorPeerId = options.actorPeerId ?? cfg.peerId ?? "";
  const log = options.log || (() => {});
  const trimmed = String(query || "").trim();
  if (!trimmed) return null;

  // Assembly happens server-side when the deployment offers the context face;
  // older servers fall through to /recall, then to raw find.
  const serverBlock = await buildServerAssembledBlock(fetchJSON, cfg, trimmed, {
    ...options,
    actorPeerId,
    log,
  });
  if (serverBlock !== null) return serverBlock || null;

  const recallLimit = Math.max(1, Number(cfg.recallLimit || DEFAULT_CONTEXT_LIMIT));
  const perSourceLimit = Math.max(recallLimit * 2, 8);
  const raw = await searchAllSources(fetchJSON, trimmed, perSourceLimit, actorPeerId, log);
  if (raw.length === 0) return null;

  const profile = buildQueryProfile(trimmed);
  const scoreThreshold = Number.isFinite(Number(cfg.scoreThreshold)) ? Number(cfg.scoreThreshold) : 0.35;
  const filtered = raw.filter((it) => clampScore(it.score) >= scoreThreshold);
  filtered.sort((a, b) => rankItem(b, profile) - rankItem(a, profile));
  const picked = dedupeItems(filtered).slice(0, recallLimit);
  log("recall_picked", {
    rawCount: raw.length,
    filteredCount: filtered.length,
    pickedCount: picked.length,
    items: picked.map((it) => ({ type: it._sourceType, uri: it.uri, score: clampScore(it.score) })),
  });

  if (picked.length === 0) return null;
  return buildFallbackInjectionBlock(fetchJSON, picked, cfg, actorPeerId, log);
}
