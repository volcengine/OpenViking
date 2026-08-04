// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
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

let userSpaceCache = "";

export function estimateTokens(text) {
  return text ? Math.ceil(String(text).length / 4) : 0;
}

export function buildRecallEndpointBody(cfg = {}) {
  const limit = Math.max(Number(cfg.recallLimit || 0), 1);
  const body = {
    query: "",
    quotas: {
      events: limit,
      entities: limit,
      preferences: Math.max(1, Math.min(limit, 3)),
      experiences: 0,
    },
    max_chars: Math.max(Number(cfg.recallMaxContentChars || 0) * limit, 1000),
    min_score: Number.isFinite(Number(cfg.scoreThreshold)) ? Number(cfg.scoreThreshold) : 0.35,
    render: true,
  };
  if (cfg.recallPeerScope === "actor") body.peer_scope = "actor";
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
  return [
    "<openviking-context>",
    "Relevant memory from OpenViking. Use the recall/read MCP tools to expand URIs.",
    rendered,
    "</openviking-context>",
  ].join("\n");
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

  const endpointBlock = await recallViaEndpoint(fetchJSON, cfg, trimmed, actorPeerId, log);
  if (endpointBlock !== null) return endpointBlock || null;

  const recallLimit = Math.max(1, Number(cfg.recallLimit || 6));
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
