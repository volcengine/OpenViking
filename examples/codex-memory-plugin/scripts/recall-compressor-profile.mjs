import { readFile, rm } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { mkdir, rename, writeFile } from "node:fs/promises";
import { getStateDir } from "./session-state.mjs";

const DEFAULT_PRIMARY = { model: "gpt-5.3-codex-spark", thinking: "default", source: "default_primary" };
const DEFAULT_FALLBACK = { model: "gpt-5.5", thinking: "low", source: "default_fallback" };
const PROFILE_SCHEMA_VERSION = 2;
const DEFAULT_CODEX_HOME = join(homedir(), ".codex");

function isOff(value) {
  return /^(?:0|false|no|off|none|disabled)$/i.test(String(value || "").trim());
}

function normalizeThinking(value) {
  const thinking = String(value || "").trim().toLowerCase();
  if (!thinking || thinking === "default") return "default";
  return thinking;
}

function normalizeModel(value) {
  return String(value || "").trim();
}

export function recallCompressionExplicitlyOff(cfg) {
  return !cfg.recallCompress || isOff(cfg.recallCompressModel) || isOff(cfg.recallCompressThinking);
}

export function buildCodexExecArgs(profile, outputPath) {
  const args = [];
  if (profile.model) args.push("-m", profile.model);
  if (profile.thinking && profile.thinking !== "default") {
    args.push("-c", `model_reasoning_effort=${JSON.stringify(profile.thinking)}`);
  }
  args.push(
    "--sandbox",
    "read-only",
    "--ask-for-approval",
    "never",
    "exec",
    "--ephemeral",
    "--ignore-user-config",
    "--skip-git-repo-check",
    "--output-last-message",
    outputPath,
    "-",
  );
  return args;
}

export function buildRecallCompressorCandidates(cfg) {
  if (recallCompressionExplicitlyOff(cfg)) return [];

  const candidates = [];
  if (cfg.recallCompressConfigured) {
    const configuredModel = normalizeModel(cfg.recallCompressModel) || DEFAULT_PRIMARY.model;
    candidates.push({
      model: configuredModel,
      thinking: normalizeThinking(cfg.recallCompressThinking),
      source: "configured",
    });
  }

  candidates.push(DEFAULT_PRIMARY, DEFAULT_FALLBACK);

  const seen = new Set();
  return candidates.filter((candidate) => {
    const key = `${candidate.model}\n${candidate.thinking}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function codexHomeDir(env = process.env) {
  const fromEnv = String(env.CODEX_HOME || "").trim();
  return fromEnv || DEFAULT_CODEX_HOME;
}

function modelsCachePath(env = process.env) {
  return join(codexHomeDir(env), "models_cache.json");
}

/**
 * Read codex's local model catalogue. Codex CLI maintains this file using
 * its own etag-backed fetch; reading it is the cheapest way to know which
 * model slugs `codex exec` can actually invoke. Missing or unreadable cache
 * → empty result, callers should treat that as "unknown availability" and
 * fall back to best effort.
 */
export async function loadCodexModelsCache(env = process.env) {
  try {
    const raw = await readFile(modelsCachePath(env), "utf-8");
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed?.models)) return { slugs: new Set(), fetchedAt: null, present: false };
    const slugs = new Set();
    for (const entry of parsed.models) {
      if (entry && typeof entry.slug === "string" && entry.slug.trim()) {
        slugs.add(entry.slug.trim());
      }
    }
    return {
      slugs,
      fetchedAt: typeof parsed.fetched_at === "string" ? parsed.fetched_at : null,
      present: true,
    };
  } catch {
    return { slugs: new Set(), fetchedAt: null, present: false };
  }
}

export function fallbackRecallCompressorProfile(cfg) {
  const candidates = buildRecallCompressorCandidates(cfg);
  if (candidates.length === 0) {
    return { enabled: false, source: "off" };
  }
  return { enabled: true, ...candidates[0], detected: false };
}

function configKey(cfg) {
  return JSON.stringify({
    version: PROFILE_SCHEMA_VERSION,
    recallCompress: cfg.recallCompress,
    recallCompressModel: normalizeModel(cfg.recallCompressModel),
    recallCompressThinking: normalizeThinking(cfg.recallCompressThinking),
    recallCompressConfigured: cfg.recallCompressConfigured,
  });
}

function profilePath() {
  return join(getStateDir(), "recall-compressor-profile.json");
}

export async function loadCachedRecallCompressorProfile(cfg) {
  try {
    const raw = await readFile(profilePath(), "utf-8");
    const cached = JSON.parse(raw);
    if (cached?.configKey !== configKey(cfg)) return null;
    if (cfg.recallCompressDetectTtlMs > 0) {
      const age = Date.now() - Number(cached.checkedAt || 0);
      if (!Number.isFinite(age) || age > cfg.recallCompressDetectTtlMs) return null;
    }
    if (!cached.profile || typeof cached.profile !== "object") return null;
    return cached.profile;
  } catch {
    return null;
  }
}

async function saveRecallCompressorProfile(cfg, profile) {
  await mkdir(getStateDir(), { recursive: true });
  const final = profilePath();
  const tmp = `${final}.tmp`;
  await writeFile(tmp, JSON.stringify({
    schemaVersion: PROFILE_SCHEMA_VERSION,
    checkedAt: Date.now(),
    configKey: configKey(cfg),
    profile,
  }));
  await rename(tmp, final);
}

/**
 * Forget the cached compressor profile. Called when the actual `codex exec`
 * compress run fails (exit non-zero, timeout, missing model). Next caller
 * re-resolves from the current models_cache.json.
 */
export async function invalidateRecallCompressorProfileCache() {
  try {
    await rm(profilePath(), { force: true });
  } catch { /* best effort */ }
}

/**
 * Record a runtime compress failure as a disabled profile. UserPromptSubmit
 * reads the cached profile directly, so writing `enabled: false` here makes
 * subsequent UPS calls within the same codex session skip compress (and
 * fall back to deterministic digest) instead of paying ~recallCompressTimeoutMs
 * per turn on a guaranteed-to-fail spawn. The next SessionStart's cache-
 * first detect treats `source === "runtime_failed"` as a cache miss and
 * re-resolves from the current catalogue, so a transient failure does not
 * permanently disable compress across codex restarts.
 */
export async function markRecallCompressorRuntimeFailed(cfg, { failedModel = "" } = {}) {
  try {
    await saveRecallCompressorProfile(cfg, {
      enabled: false,
      source: "runtime_failed",
      failedModel: String(failedModel || ""),
    });
  } catch { /* best effort */ }
}

/**
 * Pick a compressor profile by consulting codex's local model catalogue.
 *
 * No subprocess: we read `~/.codex/models_cache.json` (codex CLI maintains
 * it via its own etag fetch) and pick the first candidate whose slug is
 * present. When the catalogue is absent (codex 0.130 pre-cache, fresh
 * install, etc.) we optimistically pick the first candidate and rely on
 * the runtime compress path to invalidate the cache on failure.
 */
export async function resolveRecallCompressorProfile(cfg, logger = {}, env = process.env) {
  const { log } = logger;

  if (recallCompressionExplicitlyOff(cfg)) {
    const profile = { enabled: false, source: "configured_off" };
    await saveRecallCompressorProfile(cfg, profile);
    log?.("compress_profile_selected", profile);
    return profile;
  }

  const candidates = buildRecallCompressorCandidates(cfg);
  if (candidates.length === 0) {
    const profile = { enabled: false, source: "no_candidates" };
    await saveRecallCompressorProfile(cfg, profile);
    log?.("compress_profile_selected", profile);
    return profile;
  }

  const catalogue = await loadCodexModelsCache(env);
  log?.("compress_profile_catalogue", {
    present: catalogue.present,
    count: catalogue.slugs.size,
    fetchedAt: catalogue.fetchedAt,
  });

  let pick = null;
  if (catalogue.present && catalogue.slugs.size > 0) {
    pick = candidates.find((c) => catalogue.slugs.has(c.model));
  }
  if (!pick) {
    // Catalogue missing or none of our candidates appear in it. Pick the
    // first candidate optimistically; if `codex exec` later fails, the
    // runtime compress path invalidates this cache and re-resolves.
    pick = candidates[0];
  }

  const detected = catalogue.present && catalogue.slugs.has(pick.model);
  const profile = { enabled: true, ...pick, detected };
  await saveRecallCompressorProfile(cfg, profile);
  log?.("compress_profile_selected", profile);
  return profile;
}

/**
 * Cache-first profile lookup used by SessionStart and UserPromptSubmit.
 *
 * SessionStart no longer probes models with a subprocess on every fire.
 * Instead it loads the cached profile and only resolves (a cheap
 * models_cache.json read) when nothing is cached or the cache is stale.
 * The runtime compress path invalidates the cache on failure, which is
 * what triggers the next re-resolve.
 */
export async function detectRecallCompressorProfile(cfg, logger = {}, env = process.env) {
  const { log } = logger;
  const cached = await loadCachedRecallCompressorProfile(cfg);
  if (cached && cached.source !== "runtime_failed") {
    log?.("compress_profile_cache_hit", cached);
    return cached;
  }
  if (cached && cached.source === "runtime_failed") {
    log?.("compress_profile_recover", { failedModel: cached.failedModel || "" });
  }
  if (!cfg.recallCompressDetectOnStartup) {
    log?.("compress_profile_skip", { reason: "detect disabled and no usable cache" });
    return cached || null;
  }
  log?.("compress_profile_resolve", { reason: cached ? "runtime_failed_recover" : "cache_miss" });
  return resolveRecallCompressorProfile(cfg, logger, env);
}
