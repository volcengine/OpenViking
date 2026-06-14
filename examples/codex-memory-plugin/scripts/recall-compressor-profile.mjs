import { spawn } from "node:child_process";
import { mkdtemp, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { getStateDir } from "./session-state.mjs";

const DEFAULT_PRIMARY = { model: "gpt-5.3-codex-spark", thinking: "default", source: "default_primary" };
const DEFAULT_FALLBACK = { model: "gpt-5.5", thinking: "low", source: "default_fallback" };
const PROFILE_SCHEMA_VERSION = 1;

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

async function probeCandidate(candidate, cfg, { logError }) {
  const tmp = await mkdtemp(join(tmpdir(), "ov-recall-profile-"));
  const outputPath = join(tmp, "last-message.txt");
  const args = buildCodexExecArgs(candidate, outputPath);

  try {
    return await new Promise((resolve) => {
      const env = {
        ...process.env,
        OPENVIKING_AUTO_RECALL: "0",
        OPENVIKING_AUTO_CAPTURE: "0",
        OPENVIKING_RECALL_COMPRESS: "0",
      };
      let done = false;
      let stderr = "";
      let timer;
      const child = spawn("codex", args, { env, stdio: ["pipe", "ignore", "pipe"] });
      const finish = (ok, error = "") => {
        if (done) return;
        done = true;
        if (timer) clearTimeout(timer);
        resolve({ ok, error });
      };
      timer = setTimeout(() => {
        try {
          child.kill("SIGKILL");
        } catch { /* best effort */ }
        finish(false, `timed out after ${cfg.recallCompressDetectTimeoutMs}ms`);
      }, cfg.recallCompressDetectTimeoutMs);

      child.stderr.on("data", (chunk) => {
        stderr += chunk.toString();
        if (stderr.length > 4000) stderr = stderr.slice(-4000);
      });
      child.on("error", (err) => {
        logError?.("compress_profile_probe_spawn", err);
        finish(false, String(err?.message || err));
      });
      child.on("close", async (code) => {
        if (done) return;
        if (code !== 0) {
          finish(false, stderr.trim().slice(-1000) || `codex exited ${code}`);
          return;
        }
        try {
          const text = await readFile(outputPath, "utf-8");
          finish(/\bOK\b/i.test(text), /\bOK\b/i.test(text) ? "" : "probe output missing OK");
        } catch (err) {
          finish(false, String(err?.message || err));
        }
      });
      child.stdin.end("Reply exactly: OK");
    });
  } finally {
    await rm(tmp, { recursive: true, force: true }).catch(() => {});
  }
}

export async function detectRecallCompressorProfile(cfg, logger = {}) {
  const { log, logError } = logger;
  if (!cfg.recallCompressDetectOnStartup) {
    log?.("compress_profile_skip", { reason: "detect disabled" });
    return loadCachedRecallCompressorProfile(cfg);
  }

  const cached = await loadCachedRecallCompressorProfile(cfg);
  if (cached) {
    log?.("compress_profile_cache_hit", cached);
    return cached;
  }

  if (recallCompressionExplicitlyOff(cfg)) {
    const profile = { enabled: false, source: "configured_off" };
    await saveRecallCompressorProfile(cfg, profile);
    log?.("compress_profile_selected", profile);
    return profile;
  }

  for (const candidate of buildRecallCompressorCandidates(cfg)) {
    log?.("compress_profile_probe", candidate);
    const result = await probeCandidate(candidate, cfg, { logError });
    if (result.ok) {
      const profile = { enabled: true, detected: true, ...candidate };
      await saveRecallCompressorProfile(cfg, profile);
      log?.("compress_profile_selected", profile);
      return profile;
    }
    logError?.("compress_profile_probe_failed", { candidate, error: result.error });
  }

  const profile = { enabled: false, source: "no_available_model" };
  await saveRecallCompressorProfile(cfg, profile);
  log?.("compress_profile_selected", profile);
  return profile;
}
