#!/usr/bin/env node

/**
 * Explicit historical transcript backfill for Codex.
 *
 * This is intentionally not a hook: large historical transcripts do not fit
 * Codex's short Stop-hook timeout. Run this manually when you want old
 * transcript history imported into OpenViking.
 */

import { createHash } from "node:crypto";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";
import {
  extractTextFromPayload,
  isAssistantSideCaptureRole,
  normalizeCaptureRole,
  shouldCaptureText,
} from "./capture-utils.mjs";
import { loadConfig } from "./config.mjs";
import { deriveOvSessionId } from "./session-state.mjs";

const cfg = loadConfig();
const DEFAULT_BACKFILL_STATE_DIR = join(homedir(), ".openviking", "codex-plugin-backfill-state");

function usage() {
  process.stderr.write(`Usage:
  node scripts/backfill-transcript.mjs \\
    --session-id <codex-session-id> \\
    --transcript <rollout.jsonl> \\
    [--batch-size 100] [--no-commit] [--same-session] [--ov-session-id <id>] [--reset]

Options:
  --session-id       Codex session id. Used to derive the target OV session.
  --transcript       Codex rollout JSONL or JSON array transcript path.
  --batch-size       Number of turns to append before a progress log. Default: 100.
  --no-commit        Do not commit after appending. By default backfill commits.
  --same-session     Write to cx-<session-id> instead of cx-<session-id>-backfill.
  --ov-session-id    Explicit target OpenViking session id.
  --state-dir        Backfill progress directory. Default: ~/.openviking/codex-plugin-backfill-state.
  --reset            Clear this backfill job's saved progress before running.
  --dry-run          Parse and report counts without writing to OpenViking.
`);
}

function parseArgs(argv) {
  const out = {
    batchSize: 100,
    commit: true,
    sameSession: false,
    reset: false,
    dryRun: false,
    stateDir: process.env.OPENVIKING_CODEX_BACKFILL_STATE_DIR || DEFAULT_BACKFILL_STATE_DIR,
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
    } else if (arg === "--batch-size") {
      out.batchSize = Number(argv[++i]);
    } else if (arg === "--commit") {
      out.commit = true;
    } else if (arg === "--no-commit") {
      out.commit = false;
    } else if (arg === "--same-session") {
      out.sameSession = true;
    } else if (arg === "--ov-session-id") {
      out.ovSessionId = argv[++i];
    } else if (arg === "--state-dir") {
      out.stateDir = argv[++i];
    } else if (arg === "--reset") {
      out.reset = true;
    } else if (arg === "--dry-run") {
      out.dryRun = true;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  if (!out.sessionId) throw new Error("--session-id is required");
  if (!out.transcript) throw new Error("--transcript is required");
  if (!Number.isFinite(out.batchSize) || out.batchSize < 1) {
    throw new Error("--batch-size must be a positive integer");
  }
  out.batchSize = Math.floor(out.batchSize);
  out.transcript = resolvePath(out.transcript.replace(/^~/, homedir()));
  out.stateDir = resolvePath(out.stateDir.replace(/^~/, homedir()));
  return out;
}

function safeId(value) {
  return String(value).replace(/[^a-zA-Z0-9_-]/g, "_");
}

function scopeSuffix(value) {
  const scope = String(value || "").trim();
  if (!scope) return "";
  return createHash("sha256").update(scope).digest("hex").slice(0, 16);
}

function targetOvSessionId(args) {
  if (args.ovSessionId) return args.ovSessionId;
  const base = deriveOvSessionId(args.sessionId);
  return args.sameSession ? base : `${base}-backfill`;
}

function statePath(args, ovSessionId) {
  const suffix = scopeSuffix(cfg.stateScope);
  const scoped = suffix ? `__${suffix}` : "";
  return join(args.stateDir, `${safeId(args.sessionId)}__${safeId(ovSessionId)}${scoped}.json`);
}

function defaultState(args, ovSessionId) {
  return {
    sessionId: args.sessionId,
    ovSessionId,
    transcript: args.transcript,
    capturedTurnCount: 0,
    committedAt: null,
    createdAt: Date.now(),
    lastUpdatedAt: Date.now(),
  };
}

async function loadBackfillState(args, ovSessionId) {
  try {
    const raw = await readFile(statePath(args, ovSessionId), "utf-8");
    const parsed = JSON.parse(raw);
    if (parsed.transcript !== args.transcript) return defaultState(args, ovSessionId);
    return { ...defaultState(args, ovSessionId), ...parsed };
  } catch {
    return defaultState(args, ovSessionId);
  }
}

async function saveBackfillState(args, state) {
  await mkdir(args.stateDir, { recursive: true });
  const next = { ...state, lastUpdatedAt: Date.now() };
  const final = statePath(args, state.ovSessionId);
  const tmp = `${final}.tmp`;
  await writeFile(tmp, JSON.stringify(next));
  await rename(tmp, final);
}

async function resetBackfillState(args, ovSessionId) {
  await rm(statePath(args, ovSessionId), { force: true });
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
    turns.push({
      role,
      text: decision.text,
    });
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
  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (err) {
    process.stderr.write(`${err.message}\n\n`);
    usage();
    return 2;
  }

  const ovSessionId = targetOvSessionId(args);
  if (args.reset) await resetBackfillState(args, ovSessionId);

  const turns = await readTranscriptTurns(args.transcript);
  const state = await loadBackfillState(args, ovSessionId);
  const pending = Math.max(0, turns.length - state.capturedTurnCount);

  process.stdout.write(JSON.stringify({
    event: "start",
    baseUrl: cfg.baseUrl,
    sessionId: args.sessionId,
    ovSessionId,
    transcript: args.transcript,
    totalTurns: turns.length,
    capturedTurnCount: state.capturedTurnCount,
    pendingTurns: pending,
    batchSize: args.batchSize,
    commit: args.commit,
    dryRun: args.dryRun,
  }) + "\n");

  if (args.dryRun) return 0;

  await fetchJSON("/health");

  let appended = 0;
  for (let index = state.capturedTurnCount; index < turns.length; index += 1) {
    await appendOneTurn(ovSessionId, turns[index]);
    state.capturedTurnCount = index + 1;
    await saveBackfillState(args, state);
    appended += 1;
    if (appended % args.batchSize === 0 || state.capturedTurnCount === turns.length) {
      process.stdout.write(JSON.stringify({
        event: "progress",
        ovSessionId,
        appended,
        capturedTurnCount: state.capturedTurnCount,
        totalTurns: turns.length,
        remainingTurns: turns.length - state.capturedTurnCount,
      }) + "\n");
    }
  }

  if (args.commit && state.capturedTurnCount === turns.length) {
    if (state.committedAt) {
      process.stdout.write(JSON.stringify({
        event: "commit_skipped",
        reason: "already committed",
        ovSessionId,
        committedAt: state.committedAt,
      }) + "\n");
    } else {
      const result = await commitSession(ovSessionId);
      state.committedAt = Date.now();
      await saveBackfillState(args, state);
      process.stdout.write(JSON.stringify({
        event: "commit",
        ovSessionId,
        taskId: result?.task_id,
        status: result?.status,
        archived: result?.archived,
      }) + "\n");
    }
  }

  process.stdout.write(JSON.stringify({
    event: "done",
    ovSessionId,
    appended,
    capturedTurnCount: state.capturedTurnCount,
    totalTurns: turns.length,
  }) + "\n");
  return 0;
}

main()
  .then((code) => { process.exitCode = code; })
  .catch((err) => {
    process.stderr.write(`${err?.stack || err}\n`);
    process.exitCode = 1;
  });
