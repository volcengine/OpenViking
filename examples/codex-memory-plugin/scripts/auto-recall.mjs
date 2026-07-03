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

import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import {
  buildCodexExecArgs,
  fallbackRecallCompressorProfile,
  loadCachedRecallCompressorProfile,
  markRecallCompressorRuntimeFailed,
} from "./recall-compressor-profile.mjs";
import { deriveOvSessionId } from "./session-state.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("auto-recall");

let emitted = false;
let activeCompressor = null;
let recallDeadline = null;

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

async function fetchJSON(path, init = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), cfg.timeoutMs);
  try {
    const headers = { "Content-Type": "application/json" };
    if (cfg.apiKey) {
      headers["Authorization"] = `Bearer ${cfg.apiKey}`;
      headers["X-API-Key"] = cfg.apiKey;
    }
    if (cfg.sendIdentityHeaders && cfg.account) headers["X-OpenViking-Account"] = cfg.account;
    if (cfg.sendIdentityHeaders && cfg.user) headers["X-OpenViking-User"] = cfg.user;
    if (cfg.peerId) headers["X-OpenViking-Actor-Peer"] = cfg.peerId;
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

async function searchScope(query, targetUri, limit, bucket = "memories", sessionId = null) {
  const body = { query, target_uri: targetUri, limit, score_threshold: 0 };
  if (sessionId) body.session_id = sessionId;
  const result = await fetchJSON("/api/v1/search/search", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return result?.[bucket] || [];
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
    if (result && typeof result === "string" && result.trim()) return result.trim();
  } catch { /* fallback */ }
  return null;
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

async function getRecallCompressorProfile() {
  const cached = await loadCachedRecallCompressorProfile(cfg);
  if (cached) return cached;
  const fallback = fallbackRecallCompressorProfile(cfg);
  log("compress_profile_cache_miss", fallback);
  return fallback;
}

async function runCodexCompressor(prompt, profile) {
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
      let done = false;
      let timedOut = false;
      let stderr = "";
      const child = spawn("codex", args, { env, stdio: ["pipe", "ignore", "pipe"] });
      activeCompressor = child;
      const finish = (value, { runtimeFailed = false } = {}) => {
        if (done) return;
        done = true;
        if (activeCompressor === child) activeCompressor = null;
        clearTimeout(timer);
        if (runtimeFailed) {
          // Mark the profile as runtime_failed so subsequent UPS calls in
          // this same codex session skip compress (avoids burning
          // ~recallCompressTimeoutMs per turn on a guaranteed-to-fail
          // spawn). Next SessionStart's cache-first detect treats this
          // marker as a cache miss and re-resolves against the current
          // catalogue, so a transient failure self-recovers across codex
          // restarts. Best-effort write; failure is non-fatal.
          markRecallCompressorRuntimeFailed(cfg, { failedModel: profile.model || "" })
            .catch(() => {});
        }
        resolve(value);
      };
      const timer = setTimeout(() => {
        timedOut = true;
        logError("compress_timeout", `timed out after ${cfg.recallCompressTimeoutMs}ms`);
        try {
          child.kill("SIGKILL");
        } catch { /* best effort */ }
      }, cfg.recallCompressTimeoutMs);

      child.stderr.on("data", (chunk) => {
        stderr += chunk.toString();
        if (stderr.length > 4000) stderr = stderr.slice(-4000);
      });
      child.on("error", (err) => {
        logError("compress_spawn", err);
        finish(null, { runtimeFailed: true });
      });
      child.on("close", async (code) => {
        if (timedOut) {
          finish(null, { runtimeFailed: true });
          return;
        }
        if (code !== 0) {
          logError("compress_exit", {
            profile,
            error: stderr.trim().slice(-1000) || `codex exited ${code}`,
          });
          finish(null, { runtimeFailed: true });
          return;
        }
        try {
          finish(await readFile(outputPath, "utf-8"));
        } catch (err) {
          logError("compress_read", err);
          finish(null, { runtimeFailed: true });
        }
      });
      child.stdin.end(prompt);
    });
  } finally {
    await rm(tmp, { recursive: true, force: true }).catch(() => {});
  }
}

async function compressMemoryContext(userPrompt, items) {
  if (!cfg.recallCompress) return null;
  const profile = await getRecallCompressorProfile();
  if (!profile.enabled) {
    log("compress_skip", { reason: "profile disabled", profile });
    return null;
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
  const raw = await runCodexCompressor(prompt, profile);
  if (raw === null) return null;
  const compressed = normalizeCompressedContext(raw);
  log("compressed", { inputCount: items.length, chars: compressed.length, profile });
  return compressed;
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
  const allMemories = await searchAll(userPrompt, candidateLimit, recallSessionId);
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

  const memoryItems = await Promise.all(
    memories.map(async (item) => {
      let text = (item.abstract || item.overview || item.uri).trim();
      if (item.level === 2) {
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

  const compressedContext = await compressMemoryContext(userPrompt, memoryItems);
  const memoryContext = compressedContext === null ? fallbackDigest(memoryItems) : compressedContext;

  emit(memoryContext);
}

main().catch((err) => { logError("uncaught", err); emit(); });
