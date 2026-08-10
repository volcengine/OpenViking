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

import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadConfig } from "./config.mjs";
import { trySpawnCodex } from "./codex-launch.mjs";
import { createLogger } from "./debug-log.mjs";
import {
  buildRecallCompressorCandidates,
  buildCodexExecArgs,
  cacheRecallCompressorProfile,
  fallbackRecallCompressorProfile,
  loadCachedRecallCompressorProfile,
  markRecallCompressorRuntimeFailed,
  recallCompressionExplicitlyOff,
} from "./recall-compressor-profile.mjs";
import { deriveOvSessionId } from "./session-state.mjs";
import {
  buildRecallEndpointBody,
  fetchAssembledContext,
  normalizeContextEntry,
  postRecall,
} from "./shared/recall-core.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("auto-recall");
const effectivePeer = resolveEffectivePeerId({ cfg, cwd: process.cwd() });

let emitted = false;
let activeCompressor = null;
let recallDeadline = null;
const DEFAULT_FINAL_RECALL_CHARS = 6500;
const EXCLUDED_EXPERIENCE_STATUSES = new Set(["deprecated", "archived"]);
const EXPERIENCE_SIDECAR_FILENAMES = new Set([".abstract.md", ".overview.md", ".relations.json"]);

function output(obj, exitAfter = false) {
  if (emitted) return;
  emitted = true;
  if (recallDeadline) clearTimeout(recallDeadline);
  const line = JSON.stringify(obj) + "\n";
  if (exitAfter) {
    process.stdout.write(line, () => process.exit(0));
    return;
  }
  process.stdout.write(line);
}

function wrapRecallContext(additionalContext) {
  const body = sanitizeInjectedText(additionalContext).trim();
  if (!body) return "";
  return [
    '<openviking-context source="auto-recall" format="digest">',
    body,
    "</openviking-context>",
  ].join("\n");
}

function emit(additionalContext) {
  if (!additionalContext) {
    output({});
    return;
  }
  const wrappedContext = wrapRecallContext(additionalContext);
  if (!wrappedContext) {
    output({});
    return;
  }
  output({
    hookSpecificOutput: {
      hookEventName: "UserPromptSubmit",
      additionalContext: wrappedContext,
    },
  });
}

recallDeadline = setTimeout(() => {
  logError("recall_timeout", `timed out after ${cfg.recallTimeoutMs}ms`);
  try {
    activeCompressor?.kill("SIGKILL");
  } catch { /* best effort */ }
  output({}, true);
}, cfg.recallTimeoutMs);
recallDeadline.unref?.();

async function fetchJSON(path, init = {}, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(
    () => controller.abort(),
    Math.max(1000, Number(options.timeoutMs) || cfg.timeoutMs),
  );
  try {
    const headers = { "Content-Type": "application/json" };
    if (cfg.apiKey) {
      headers["Authorization"] = `Bearer ${cfg.apiKey}`;
      headers["X-API-Key"] = cfg.apiKey;
    }
    if (cfg.sendIdentityHeaders && cfg.account) headers["X-OpenViking-Account"] = cfg.account;
    if (cfg.sendIdentityHeaders && cfg.user) headers["X-OpenViking-User"] = cfg.user;
    if (effectivePeer.peerId) headers["X-OpenViking-Actor-Peer"] = effectivePeer.peerId;
    if (cfg.userAgent) headers["User-Agent"] = cfg.userAgent;
    const res = await fetch(`${cfg.baseUrl}${path}`, { ...init, headers, signal: controller.signal });
    const body = await res.json().catch(() => null);
    if (!body) return { ok: false, status: res.status };
    if (!res.ok || body.status === "error") {
      return { ok: false, status: res.status, error: body.error || body };
    }
    return { ok: true, result: body.result ?? body };
  } catch {
    return { ok: false, status: 0 };
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

async function searchScope(query, targetUri, limit, bucket = "memories", sessionId = null) {
  const body = { query, target_uri: targetUri, limit, score_threshold: 0 };
  if (sessionId) body.session_id = sessionId;
  const result = await fetchJSON("/api/v1/search/search", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return result.ok ? (result.result?.[bucket] || []) : [];
}

// Candidate target URIs for a bucket, most-specific first. In trusted mode a
// user's memories live under viking://user/<user>/<bucket>; in api_key mode the
// server canonicalizes the generic viking://user/<bucket> to the authenticated
// user. Trying the user-scoped path first with the generic path as a fallback
// recalls correctly in both modes. De-duped so a missing/blank user never
// doubles the request count.
function userScopedTargets(kind) {
  const suffix = kind.replace(/^\/+/, "");
  const targets = [`viking://user/${suffix}`];
  if (cfg.user) {
    targets.unshift(`viking://user/${cfg.user}/${suffix}`);
  }
  return [...new Set(targets)];
}

// Two-phase, short-circuiting search over the candidate targets:
//   1) a session-scoped pass (uses OpenViking's session-aware planner);
//   2) only if the entire session pass is empty, a single session-independent
//      pass (the planner can legitimately decide that no extra context is
//      needed for this session, but auto-recall still needs a memory lookup).
// Each phase stops at the first non-empty target, so a warm user costs one
// request and the worst case is bounded by (targets x 2) — instead of running
// a per-target session+fallback for every target.
async function searchBucket(query, targetUris, limit, bucket, sessionId = null) {
  for (const targetUri of targetUris) {
    const items = await searchScope(query, targetUri, limit, bucket, sessionId);
    if (items.length > 0) return items;
  }
  if (!sessionId) return [];
  for (const targetUri of targetUris) {
    const items = await searchScope(query, targetUri, limit, bucket, null);
    if (items.length > 0) return items;
  }
  return [];
}

async function searchAll(query, limit, sessionId = null) {
  const [userMems, userSkills] = await Promise.all([
    searchBucket(query, userScopedTargets("memories"), limit, "memories", sessionId),
    searchBucket(query, userScopedTargets("skills"), limit, "skills", sessionId),
  ]);
  log("search_complete", { scope: "user", rawCount: userMems.length, topScores: userMems.slice(0, 3).map((m) => m.score) });
  log("search_complete", { scope: "skills", rawCount: userSkills.length, topScores: userSkills.slice(0, 3).map((m) => m.score) });
  const all = [...userMems, ...userSkills];
  const seen = new Set();
  return all.filter((m) => {
    if (seen.has(m.uri)) return false;
    seen.add(m.uri);
    return true;
  });
}

function resolveRecallSessionId(codexSessionId) {
  if (!codexSessionId) return null;
  // Derive directly: the OV session id is deterministic (cx-<safe-id>), so
  // recall does not need to read plugin state. This keeps the recall hook
  // crash-free even if the state file is corrupt/missing, and stays in sync
  // with capture, which now also derives cx-* unconditionally.
  return deriveOvSessionId(codexSessionId);
}

async function readMemoryContent(uri) {
  try {
    const result = await fetchJSON(`/api/v1/content/read?uri=${encodeURIComponent(uri)}`);
    if (result.ok && typeof result.result === "string" && result.result.trim()) return result.result.trim();
  } catch { /* fallback */ }
  return null;
}

function isRecord(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function experienceUriInfo(uri) {
  const value = String(uri || "").trim();
  const looksLikeExperience = /^viking:\/\//i.test(value)
    && /\/memories\/experiences(?:\/|$)/i.test(value);
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return { isExperience: looksLikeExperience, canonical: false, value };
  }
  if (parsed.protocol !== "viking:") {
    return { isExperience: looksLikeExperience, canonical: false, value };
  }
  const parts = [parsed.hostname, ...parsed.pathname.split("/").filter(Boolean)];
  let memoryRoot = -1;
  if (parts[0] === "user") {
    if (parts.length > 5 && parts[2] === "peers" && parts[4] === "memories") memoryRoot = 4;
    else if (parts.length > 3 && parts[2] === "memories") memoryRoot = 2;
    else if (parts.length > 4 && parts[1] === "peers" && parts[3] === "memories") memoryRoot = 3;
    else if (parts.length > 2 && parts[1] === "memories") memoryRoot = 1;
  } else if (parts.length > 3 && parts[0] === "agent" && parts[2] === "memories") {
    memoryRoot = 2;
  }
  if (memoryRoot < 0 || parts[memoryRoot + 1] !== "experiences") {
    // Fail closed for malformed/unknown Viking namespaces that still visibly
    // target the Experience directory; treating them as ordinary memories
    // would bypass authoritative lifecycle hydration.
    return { isExperience: looksLikeExperience, canonical: false, value };
  }
  const relative = parts.slice(memoryRoot + 2);
  const basename = relative.at(-1) || "";
  const canonical = Boolean(
    !parsed.search
    && !parsed.hash
    && relative.length > 0
    && relative.every((segment) => segment && segment !== "." && segment !== "..")
    && !EXPERIENCE_SIDECAR_FILENAMES.has(basename),
  );
  return { isExperience: true, canonical, value };
}

function normalizedStatus(...sources) {
  let status = "";
  for (const source of sources) {
    if (isRecord(source) && typeof source.status === "string" && source.status.trim()) {
      status = source.status.trim().toLowerCase();
    }
  }
  return status;
}

function parseAuthoritativeExperienceDocument(value) {
  const objectValue = isRecord(value) ? value : null;
  const raw = objectValue
    ? objectValue.raw_content ?? objectValue.raw ?? objectValue.content
    : value;
  if (typeof raw !== "string" || !raw.trim()) return null;

  const hasMemoryFieldsMarker = /<!--\s*MEMORY_FIELDS\b/i.test(raw);
  const trailer = /<!--\s*MEMORY_FIELDS\b([\s\S]*?)\s*-->\s*$/i.exec(raw);
  let fields = null;
  if (hasMemoryFieldsMarker) {
    if (!trailer) return null;
    try {
      fields = JSON.parse(trailer[1].trim());
    } catch {
      return null;
    }
    if (!isRecord(fields)) return null;
  } else if (objectValue) {
    // Some server versions return raw content and authoritative metadata as
    // separate JSON members. Legacy documents can also have no lifecycle
    // metadata at all; that is an eligible unknown status, not a parse error.
    if (isRecord(objectValue.attrs)) fields = objectValue.attrs;
    if (isRecord(objectValue.metadata)) fields = { ...(fields || {}), ...objectValue.metadata };
    if (Object.hasOwn(objectValue, "status")) fields = { ...(fields || {}), status: objectValue.status };
  } else {
    // A non-empty legacy raw string without MEMORY_FIELDS has status="".
    fields = {};
  }

  const content = (trailer ? raw.slice(0, trailer.index) : raw).trim();
  if (!content) return null;
  return {
    content,
    status: normalizedStatus(objectValue?.attrs, objectValue?.metadata, objectValue, fields),
  };
}

async function readAuthoritativeExperience(uri) {
  const info = experienceUriInfo(uri);
  if (!info.isExperience) return { isExperience: false, document: null };
  if (!info.canonical) {
    log("experience_drop", { uri: info.value, reason: "noncanonical_uri" });
    return { isExperience: true, document: null };
  }
  const result = await fetchJSON(
    `/api/v1/content/read?uri=${encodeURIComponent(info.value)}&raw=true`,
  );
  if (!result.ok) {
    log("experience_drop", { uri: info.value, reason: "raw_read_failed", status: result.status || 0 });
    return { isExperience: true, document: null };
  }
  const document = parseAuthoritativeExperienceDocument(result.result);
  if (!document) {
    log("experience_drop", { uri: info.value, reason: "invalid_or_empty_raw_metadata" });
    return { isExperience: true, document: null };
  }
  if (EXCLUDED_EXPERIENCE_STATUSES.has(document.status)) {
    log("experience_drop", { uri: info.value, reason: "lifecycle_status", status: document.status });
    return { isExperience: true, document: null };
  }
  return { isExperience: true, document };
}

async function enforceExperienceLifecycle(items) {
  const checked = await Promise.all(items.map(async (item) => {
    const result = await readAuthoritativeExperience(item?.uri);
    if (!result.isExperience) return { item, experienceContent: null };
    if (!result.document) return null;
    return { item, experienceContent: result.document.content };
  }));
  const kept = checked.filter(Boolean);
  return { kept, filtered: kept.length !== items.length };
}

async function assembledToRecallResult(rendered, entries) {
  const normalizedItems = entries
    .map(normalizeContextEntry)
    .map((entry) => ({ ...entry, score: clampScore(entry.score) }))
    .filter((entry) => entry.uri && entry.text);
  const lifecycle = await enforceExperienceLifecycle(normalizedItems);
  const items = lifecycle.kept.map(({ item, experienceContent }) => (
    experienceContent === null ? item : { ...item, text: experienceContent }
  ));
  const renderedContext = rendered
    ? [
        "OpenViking memory digest:",
        rendered,
        "",
        "More detail: use the OpenViking MCP recall/read/search tools with cited viking:// URIs if needed.",
      ].join("\n")
    : "";
  // Once any entry is removed, the server-rendered block is no longer safe: it
  // still contains the excluded entry's body. Rebuild only from retained items.
  const context = lifecycle.filtered || normalizedItems.length !== entries.length
    ? fallbackDigest(items)
    : renderedContext;
  return { context, items };
}

async function recallViaServerAssembly(query, ovSessionId = "") {
  const maxInputChars = cfg.recallCompress
    ? cfg.recallCompressMaxInputChars
    : DEFAULT_FINAL_RECALL_CHARS;
  const assembleCfg = {
    ...cfg,
    // Local compression happens below, so ask the server for the assembled
    // block only. The server budget stays independent from the compressor's
    // input-character ceiling.
    recallRewrite: "off",
  };

  const assembled = await fetchAssembledContext(fetchJSON, assembleCfg, query, {
    actorPeerId: effectivePeer.peerId,
    sessionId: ovSessionId,
    log,
  });
  if (assembled) {
    return await assembledToRecallResult(assembled.rendered, assembled.entries);
  }

  const body = buildRecallEndpointBody(cfg);
  body.query = query;
  body.max_chars = maxInputChars;
  const result = await postRecall(fetchJSON, body, { actorPeerId: effectivePeer.peerId, log });
  if (!result.ok) {
    log("recall_endpoint_fallback", { status: result.status || 0 });
    return null;
  }
  return await assembledToRecallResult(
    String(result.result?.rendered || "").trim(),
    Array.isArray(result.result?.entries) ? result.result.entries : [],
  );
}

function truncateText(text, maxChars) {
  const value = String(text || "").trim();
  if (value.length <= maxChars) return value;
  return `${value.slice(0, Math.max(0, maxChars - 20)).trimEnd()}\n[truncated]`;
}

function sanitizeInjectedText(text) {
  return String(text || "")
    .replace(/<\/?relevant-memor(?:y|ies)\b[^>]*>/gi, "legacy memory wrapper")
    .replace(/<\/?openviking-context\b[^>]*>/gi, "openviking context marker");
}

function isNoRelevantMemory(text) {
  const value = String(text || "")
    .trim()
    .replace(/^openviking memory digest:\s*/i, "")
    .trim();
  return !value || /^NO_RELEVANT_MEMORY\.?$/i.test(value) || /^no (?:directly )?relevant memor(?:y|ies)\.?$/i.test(value);
}

function hasDigestSignal(text) {
  const body = String(text || "").replace(/^openviking memory digest:\s*/i, "").trim();
  return /(^|\n)\s*[-*]\s+\S/.test(body) || /\bviking:\/\//i.test(body);
}

function appendMcpRetrievalHint(text) {
  const value = String(text || "").trim();
  if (!/\bviking:\/\//i.test(value) || /OpenViking MCP/i.test(value)) return value;
  return `${value}\n\nMore detail: use the OpenViking MCP read/search tools with the cited viking:// URI if needed.`;
}

function fallbackDigest(items) {
  const lines = items.slice(0, cfg.recallCompressMaxBullets).map((item) => {
    const text = sanitizeInjectedText(truncateText(item.text, 260)).replace(/\s+/g, " ");
    return `- [${item.category || "memory"}] ${text} (${item.uri})`;
  });
  return lines.length > 0 ? appendMcpRetrievalHint(`OpenViking memory digest:\n${lines.join("\n")}`) : "";
}

function normalizeCompressedContext(text) {
  let value = String(text || "").trim();
  if (!value) return "";
  value = value.replace(/^```(?:text|markdown)?\s*/i, "").replace(/\s*```$/i, "").trim();
  value = sanitizeInjectedText(value);
  if (isNoRelevantMemory(value)) return "";
  if (!value.toLowerCase().startsWith("openviking memory digest:")) {
    value = `OpenViking memory digest:\n${value}`;
  }
  if (!hasDigestSignal(value)) return "";
  return truncateText(appendMcpRetrievalHint(value), 4000);
}

async function getRecallCompressorProfiles() {
  if (recallCompressionExplicitlyOff(cfg)) return [];
  const cached = await loadCachedRecallCompressorProfile(cfg);
  const failedModels = new Set(
    cached?.source === "runtime_failed"
      ? [...(cached.failedModels || []), cached.failedModel || ""].filter(Boolean)
      : [],
  );
  const candidates = [];
  if (cached?.enabled) candidates.push(cached);
  if (!cached) {
    const fallback = fallbackRecallCompressorProfile(cfg);
    log("compress_profile_cache_miss", fallback);
    if (fallback.enabled) candidates.push(fallback);
  }
  candidates.push(...buildRecallCompressorCandidates(cfg));

  const seenModels = new Set();
  return candidates.filter((profile) => {
    if (!profile?.enabled && profile?.enabled !== undefined) return false;
    if (!profile?.model || failedModels.has(profile.model)) return false;
    if (seenModels.has(profile.model)) return false;
    seenModels.add(profile.model);
    return true;
  }).slice(0, 2);
}

async function runCodexCompressor(prompt, profile, timeoutMs) {
  const tmp = await mkdtemp(join(tmpdir(), "ov-recall-compress-"));
  const outputPath = join(tmp, "last-message.txt");
  const args = buildCodexExecArgs(profile, outputPath);

  try {
    return await new Promise((resolve) => {
      const env = {
        ...process.env,
        OPENVIKING_AUTO_RECALL: "0",
        OPENVIKING_AUTO_CAPTURE: "0",
        OPENVIKING_RECALL_COMPRESS: "0",
      };
      let child = null;
      let timer = null;
      let done = false;
      let timedOut = false;
      let stderr = "";
      const finish = (value) => {
        if (done) return;
        done = true;
        if (activeCompressor === child) activeCompressor = null;
        clearTimeout(timer);
        resolve(value);
      };
      const launch = trySpawnCodex(args, { env, stdio: ["pipe", "ignore", "pipe"] });
      if (launch.error) {
        logError("compress_spawn", launch.error);
        finish(null);
        return;
      }
      child = launch.child;
      activeCompressor = child;
      timer = setTimeout(() => {
        timedOut = true;
        logError("compress_timeout", `timed out after ${timeoutMs}ms`);
        try {
          child.kill("SIGKILL");
        } catch { /* best effort */ }
      }, timeoutMs);

      child.stderr.on("data", (chunk) => {
        stderr += chunk.toString();
        if (stderr.length > 4000) stderr = stderr.slice(-4000);
      });
      child.on("error", (err) => {
        logError("compress_spawn", err);
        finish(null);
      });
      child.on("close", async (code) => {
        if (timedOut) {
          finish(null);
          return;
        }
        if (code !== 0) {
          logError("compress_exit", {
            profile,
            error: stderr.trim().slice(-1000) || `codex exited ${code}`,
          });
          finish(null);
          return;
        }
        try {
          finish(await readFile(outputPath, "utf-8"));
        } catch (err) {
          logError("compress_read", err);
          finish(null);
        }
      });
      child.stdin.end(prompt);
    });
  } finally {
    await rm(tmp, { recursive: true, force: true }).catch(() => {});
  }
}

async function compressMemoryContext(userPrompt, items) {
  if (recallCompressionExplicitlyOff(cfg)) {
    log("compress_skip", { reason: "explicitly disabled" });
    return { status: "disabled", context: "" };
  }
  const profiles = await getRecallCompressorProfiles();
  if (profiles.length === 0) {
    log("compress_skip", { reason: "no usable profiles" });
    return { status: "failed", context: "" };
  }
  const perItemChars = Math.max(500, Math.floor(cfg.recallCompressMaxInputChars / Math.max(1, items.length)));
  const payload = {
    user_prompt: userPrompt,
    max_bullets: cfg.recallCompressMaxBullets,
    memories: items.map((item) => ({
      uri: item.uri,
      category: item.category || "memory",
      score: item.score,
      text: truncateText(item.text, perItemChars),
    })),
  };
  const prompt = `You are a memory relevance compressor for a Codex UserPromptSubmit hook.

Task:
- Keep only memories directly useful for answering the user's current prompt.
- Drop stale, generic, duplicate, merely adjacent, or operationally unrelated memories.
- Compress to at most ${cfg.recallCompressMaxBullets} short bullets.
- Preserve concrete facts, dates, paths, repo names, commands, and user preferences.
- Include the source viking:// URI when the agent may need to inspect more detail.
- If the answer needs detail beyond the bullet, say to use OpenViking MCP read/search with the cited viking:// URI if needed.
- Do not include XML/HTML wrappers.
- Do not mention that you filtered memories.
- Output either "OpenViking memory digest:" followed by useful bullets, or exactly: NO_RELEVANT_MEMORY.
- If no memory is directly useful, output exactly: NO_RELEVANT_MEMORY.

Input JSON:
${JSON.stringify(payload, null, 2)}
`;
  const failedModels = [];
  // `recallCompressTimeoutMs` is one total budget, not a per-model budget.
  // Divide the remaining time across the attempts still available so a hung
  // primary cannot consume the fallback's entire window or overrun the hook.
  const compressorDeadline = Date.now() + cfg.recallCompressTimeoutMs;
  for (const [attempt, profile] of profiles.entries()) {
    const remainingMs = compressorDeadline - Date.now();
    if (remainingMs <= 0) break;
    const attemptsLeft = profiles.length - attempt;
    const attemptTimeoutMs = Math.max(1, Math.floor(remainingMs / attemptsLeft));
    log("compress_attempt", { attempt: attempt + 1, attemptTimeoutMs, profile });
    const raw = await runCodexCompressor(prompt, profile, attemptTimeoutMs);
    if (raw === null) {
      failedModels.push(profile.model || "");
      continue;
    }
    // A runtime_failed cache can exclude the primary before this loop, making a
    // working fallback attempt 0 rather than attempt 1. Promote every successful
    // profile when the cache does not already describe that exact model/profile;
    // attempt position is not a reliable signal of whether promotion is needed.
    const cached = await loadCachedRecallCompressorProfile(cfg);
    const cachedThinking = String(cached?.thinking || "default").toLowerCase();
    const profileThinking = String(profile?.thinking || "default").toLowerCase();
    if (
      !cached?.enabled
      || cached.model !== profile.model
      || cachedThinking !== profileThinking
    ) {
      await cacheRecallCompressorProfile(cfg, profile);
    }
    const compressed = normalizeCompressedContext(raw);
    log("compressed", { inputCount: items.length, chars: compressed.length, profile });
    return { status: "ok", context: compressed };
  }

  await markRecallCompressorRuntimeFailed(cfg, { failedModels });
  log("compress_fail_closed", { failedModels });
  return { status: "failed", context: "" };
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
  const codexSessionId = typeof input.session_id === "string" ? input.session_id.trim() : "";
  const recallSessionId = resolveRecallSessionId(codexSessionId);
  log("start", {
    codexSessionId: codexSessionId || null,
    recallSessionId,
    query: userPrompt.slice(0, 200),
    queryLength: userPrompt.length,
    config: {
      recallLimit: cfg.recallLimit,
      scoreThreshold: cfg.scoreThreshold,
      peerSource: effectivePeer.source,
      recallPeerScope: cfg.recallPeerScope,
    },
  });

  if (!userPrompt || userPrompt.length < cfg.minQueryLength) {
    log("skip", { stage: "query_check", reason: "query too short or empty" });
    emit();
    return;
  }

  const health = await fetchJSON("/health");
  if (!health.ok) {
    logError("health_check", "server unreachable or unhealthy");
    emit();
    return;
  }

  const endpointRecall = await recallViaServerAssembly(userPrompt, recallSessionId || "");
  if (endpointRecall !== null) {
    if (!endpointRecall.context && endpointRecall.items.length === 0) {
      log("skip", { stage: "recall_endpoint", reason: "no results" });
      emit();
      return;
    }
    const compression = endpointRecall.items.length > 0
      ? await compressMemoryContext(userPrompt, endpointRecall.items)
      : { status: "disabled", context: "" };
    const memoryContext = endpointRecall.items.length === 0
      ? endpointRecall.context
      : compression.status === "disabled"
        ? (cfg.recallCompress ? fallbackDigest(endpointRecall.items) : endpointRecall.context)
        : compression.context;
    if (!memoryContext) {
      log("skip", { stage: "recall_endpoint", reason: "compressor found no relevant memory" });
      emit();
      return;
    }
    log("recall_endpoint", {
      chars: memoryContext.length,
      compressed: compression.status === "ok",
      entryCount: endpointRecall.items.length,
    });
    emit(memoryContext);
    return;
  }

  const candidateLimit = Math.max(cfg.recallLimit * 4, 20);
  const allMemories = await searchAll(userPrompt, candidateLimit, recallSessionId);
  if (allMemories.length === 0) {
    log("skip", { stage: "search", reason: "no results" });
    emit();
    return;
  }

  const processed = postProcess(allMemories, candidateLimit, cfg.scoreThreshold);
  log("post_process", { beforeCount: allMemories.length, afterCount: processed.length });

  // Validate the full ranked candidate pool before selecting the final width.
  // Otherwise an archived top hit would consume a slot, be removed after pick,
  // and prevent a lower-ranked eligible memory from backfilling it.
  const lifecycle = await enforceExperienceLifecycle(processed);
  const eligibleProcessed = lifecycle.kept.map(({ item }) => item);
  const experienceContentByUri = new Map(
    lifecycle.kept
      .filter(({ experienceContent }) => experienceContent !== null)
      .map(({ item, experienceContent }) => [item.uri, experienceContent]),
  );
  log("experience_lifecycle", {
    beforeCount: processed.length,
    afterCount: eligibleProcessed.length,
  });

  const profile = buildQueryProfile(userPrompt);
  const ranked = [...eligibleProcessed]
    .map((item) => ({ item, breakdown: getRankingBreakdown(item, profile) }))
    .sort((a, b) => b.breakdown.finalScore - a.breakdown.finalScore);

  if (cfg.logRankingDetails) {
    for (const entry of ranked) {
      log("ranking_detail", { uri: entry.item.uri, ...entry.breakdown });
    }
  } else {
    log("ranking_summary", {
      candidateCount: eligibleProcessed.length,
      topCandidates: ranked.slice(0, 5).map((entry) => ({ uri: entry.item.uri, finalScore: entry.breakdown.finalScore })),
    });
  }

  const memories = pickMemories(eligibleProcessed, cfg.recallLimit, userPrompt);
  if (memories.length === 0) {
    log("skip", { stage: "pick", reason: "no memories survived ranking" });
    emit();
    return;
  }

  log("picked", {
    pickedCount: memories.length,
    uris: memories.map((item) => item.uri),
  });

  const memoryItems = await Promise.all(
    memories.map(async (item) => {
      let text = (item.abstract || item.overview || item.uri).trim();
      if (experienceContentByUri.has(item.uri)) {
        text = experienceContentByUri.get(item.uri);
      } else if (item.level === 2) {
        const content = await readMemoryContent(item.uri);
        if (content) text = content;
      }
      return {
        uri: item.uri,
        category: item.category || "memory",
        score: clampScore(item.score),
        text,
      };
    }),
  );

  const compression = await compressMemoryContext(userPrompt, memoryItems);
  const memoryContext = compression.status === "disabled"
    ? fallbackDigest(memoryItems)
    : compression.context;

  emit(memoryContext);
}

main().catch((err) => { logError("uncaught", err); emit(); });
