#!/usr/bin/env node

/**
 * Internal detached worker for transcript capture that is too large for a
 * Codex hook timeout. Hooks schedule this process; users should not need to
 * run it directly.
 */

import { createHash } from "node:crypto";
import { mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";
import {
  extractTextFromPayload,
  isAssistantSideCaptureRole,
  normalizeCaptureRole,
  shouldCaptureText,
} from "./capture-utils.mjs";
import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("capture-transcript-worker");
const DEFAULT_WORKER_STATE_DIR = join(homedir(), ".openviking", "codex-plugin-worker-state");

function usage() {
  process.stderr.write(`Usage:
  node scripts/capture-transcript-worker.mjs \\
    --session-id <codex-session-id> \\
    --transcript <rollout.jsonl> \\
    --ov-session-id <openviking-session-id> \\
    [--start-index 0] [--end-index <turn-count>] [--batch-size 100] [--cleanup-snapshot]
`);
}

function parseArgs(argv) {
  const out = {
    startIndex: 0,
    endIndex: null,
    batchSize: cfg.backgroundCaptureBatchSize,
    stateDir: process.env.OPENVIKING_CODEX_WORKER_STATE_DIR || DEFAULT_WORKER_STATE_DIR,
    cleanupSnapshot: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--help" || arg === "-h") {
      usage();
      process.exit(0);
    } else if (arg === "--session-id") {
      out.sessionId = argv[++i];
    } else if (arg === "--transcript") {
      out.transcript = argv[++i];
    } else if (arg === "--ov-session-id") {
      out.ovSessionId = argv[++i];
    } else if (arg === "--start-index") {
      out.startIndex = Number(argv[++i]);
    } else if (arg === "--end-index") {
      out.endIndex = Number(argv[++i]);
    } else if (arg === "--batch-size") {
      out.batchSize = Number(argv[++i]);
    } else if (arg === "--cleanup-snapshot") {
      out.cleanupSnapshot = true;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  if (!out.sessionId) throw new Error("--session-id is required");
  if (!out.transcript) throw new Error("--transcript is required");
  if (!out.ovSessionId) throw new Error("--ov-session-id is required");
  if (!Number.isFinite(out.startIndex) || out.startIndex < 0) {
    throw new Error("--start-index must be a non-negative integer");
  }
  if (out.endIndex != null && (!Number.isFinite(out.endIndex) || out.endIndex < out.startIndex)) {
    throw new Error("--end-index must be >= --start-index");
  }
  if (!Number.isFinite(out.batchSize) || out.batchSize < 1) {
    throw new Error("--batch-size must be a positive integer");
  }
  out.startIndex = Math.floor(out.startIndex);
  out.endIndex = out.endIndex == null ? null : Math.floor(out.endIndex);
  out.batchSize = Math.floor(out.batchSize);
  out.transcript = resolvePath(out.transcript.replace(/^~/, homedir()));
  out.stateDir = resolvePath(out.stateDir.replace(/^~/, homedir()));
  return out;
}

function safeId(value) {
  return String(value).replace(/[^a-zA-Z0-9_-]/g, "_");
}

function statePath(args) {
  const key = [
    args.sessionId,
    args.ovSessionId,
    args.transcript,
    String(args.startIndex),
    String(args.endIndex ?? "end"),
  ].join("|");
  const digest = createHash("sha256").update(key).digest("hex").slice(0, 16);
  return join(args.stateDir, `${safeId(args.sessionId)}__${safeId(args.ovSessionId)}__${digest}.json`);
}

function defaultState(args) {
  return {
    sessionId: args.sessionId,
    ovSessionId: args.ovSessionId,
    transcript: args.transcript,
    startIndex: args.startIndex,
    endIndex: args.endIndex,
    nextIndex: args.startIndex,
    committedAt: null,
    createdAt: Date.now(),
    lastUpdatedAt: Date.now(),
  };
}

async function loadWorkerState(args) {
  try {
    const raw = await readFile(statePath(args), "utf-8");
    const parsed = JSON.parse(raw);
    return { ...defaultState(args), ...parsed };
  } catch {
    return defaultState(args);
  }
}

async function saveWorkerState(args, state) {
  await mkdir(args.stateDir, { recursive: true });
  const next = { ...state, lastUpdatedAt: Date.now() };
  const final = statePath(args);
  const tmp = `${final}.tmp`;
  await writeFile(tmp, JSON.stringify(next));
  await rename(tmp, final);
}

async function fetchJSON(path, init = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), cfg.captureTimeoutMs);
  try {
    const headers = { "Content-Type": "application/json" };
    if (cfg.apiKey) {
      headers.Authorization = `Bearer ${cfg.apiKey}`;
      headers["X-API-Key"] = cfg.apiKey;
    }
    if (cfg.sendIdentityHeaders && cfg.account) headers["X-OpenViking-Account"] = cfg.account;
    if (cfg.sendIdentityHeaders && cfg.user) headers["X-OpenViking-User"] = cfg.user;
    if (cfg.peerId) headers["X-OpenViking-Actor-Peer"] = cfg.peerId;
    const res = await fetch(`${cfg.baseUrl}${path}`, { ...init, headers, signal: controller.signal });
    const body = await res.json().catch(() => null);
    if (!body) return null;
    if (!res.ok || body.status === "error") {
      throw new Error(`HTTP ${res.status}: ${JSON.stringify(body)}`);
    }
    return body.result ?? body;
  } finally {
    clearTimeout(timer);
  }
}

function parseTranscript(content) {
  try {
    const data = JSON.parse(content);
    if (Array.isArray(data)) return data;
  } catch { /* not a JSON array */ }
  const lines = content.split("\n").filter((line) => line.trim());
  const out = [];
  for (const line of lines) {
    try { out.push(JSON.parse(line)); } catch { /* skip invalid rollout line */ }
  }
  return out;
}

function extractTurns(entries) {
  const turns = [];
  for (const entry of entries) {
    if (!entry || typeof entry !== "object") continue;
    const payload = entry.payload && typeof entry.payload === "object" ? entry.payload : entry;
    const message = payload.message && typeof payload.message === "object" ? payload.message : null;
    const rawRole = message?.role || payload.role || payload.type || payload.kind;
    const role = normalizeCaptureRole(rawRole);
    if (!role) continue;
    if (isAssistantSideCaptureRole(rawRole) && !cfg.captureAssistantTurns) continue;

    const rawText = extractTextFromPayload(payload, { toolMaxChars: cfg.captureToolMaxChars });
    const decision = shouldCaptureText(rawText, role, cfg);
    if (!decision.shouldCapture) continue;
    turns.push({ role, text: decision.text });
  }
  return turns;
}

async function readTranscriptTurns(transcriptPath) {
  const raw = await readFile(transcriptPath, "utf-8");
  if (!raw.trim()) return [];
  return extractTurns(parseTranscript(raw));
}

async function appendOneTurn(ovSessionId, turn) {
  const body = { role: turn.role, content: turn.text };
  if (cfg.peerId) body.peer_id = cfg.peerId;
  return fetchJSON(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}/messages`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

async function commitSession(ovSessionId) {
  return fetchJSON(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}/commit`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const turns = await readTranscriptTurns(args.transcript);
  const endIndex = Math.min(args.endIndex ?? turns.length, turns.length);
  const state = await loadWorkerState({ ...args, endIndex });

  log("start", {
    baseUrl: cfg.baseUrl,
    sessionId: args.sessionId,
    ovSessionId: args.ovSessionId,
    transcript: args.transcript,
    startIndex: args.startIndex,
    endIndex,
    nextIndex: state.nextIndex,
    totalTurns: turns.length,
    batchSize: args.batchSize,
  });

  await fetchJSON("/health");

  let appended = 0;
  for (let index = state.nextIndex; index < endIndex; index += 1) {
    await appendOneTurn(args.ovSessionId, turns[index]);
    state.nextIndex = index + 1;
    await saveWorkerState({ ...args, endIndex }, state);
    appended += 1;
    if (appended % args.batchSize === 0 || state.nextIndex === endIndex) {
      log("progress", {
        ovSessionId: args.ovSessionId,
        appended,
        nextIndex: state.nextIndex,
        endIndex,
        remainingTurns: endIndex - state.nextIndex,
      });
    }
  }

  if (state.nextIndex >= endIndex && !state.committedAt) {
    const result = await commitSession(args.ovSessionId);
    state.committedAt = Date.now();
    await saveWorkerState({ ...args, endIndex }, state);
    log("commit", {
      ovSessionId: args.ovSessionId,
      taskId: result?.task_id,
      status: result?.status,
      archived: result?.archived,
    });
  }

  if (args.cleanupSnapshot && state.committedAt) {
    try {
      await unlink(args.transcript);
      log("snapshot_cleaned", { transcript: args.transcript });
    } catch (err) {
      logError("snapshot_cleanup_failed", err);
    }
  }

  log("done", {
    ovSessionId: args.ovSessionId,
    appended,
    nextIndex: state.nextIndex,
    endIndex,
  });
  return 0;
}

main()
  .then((code) => { process.exitCode = code; })
  .catch((err) => {
    logError("uncaught", err);
    process.exitCode = 1;
  });
