#!/usr/bin/env node

/**
 * The one hook entry cursor, TRAE and ZCode all run.
 *
 * Each host names it from its own hooks.json, passing the event and the client
 * id as arguments; the adapter under `hosts/` supplies everything that differs
 * — the event vocabulary, the response envelope, how a prompt is read out of
 * the payload, and how a finished turn is captured. The state machine around
 * those four things is the same in all three, so it lives here.
 */

import { spawn } from "node:child_process";
import { mkdir, readdir, readFile, rm, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

import {
  buildAgentProfile,
  createAgentLogger,
  deriveAgentSessionId,
  loadAgentHookConfig,
  makeAgentFetchJSON,
  recallForPrompt,
  readHookState,
  replayAgentPending,
  resolveNativeSessionId,
  runHookStage,
  shouldBypassAgent,
  stableHash,
  withAgentHookLock,
  writeHookState,
} from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { maybeDetach, readHookStdin } from "../../memory-plugin-shared/lib/async-writer.mjs";
import { isCaptureEnabled } from "../../memory-plugin-shared/lib/capture-utils.mjs";
import { HOSTS } from "../hosts/index.mjs";
import { withRequestBudget } from "./request-budget.mjs";

// ZCode's detached writer re-enters this file with no arguments at all, so what
// the first run put in the environment is what tells the worker what it is.
const event = process.argv[2] || process.env.OPENVIKING_HOOK_EVENT || "";
const clientId = process.argv[3] || process.env.OPENVIKING_HOOK_SOURCE || "";
const host = HOSTS[clientId];

if (!host) {
  process.stderr.write(`openviking: no hook adapter for ${clientId || "?"}\n`);
  process.exit(0);
}

process.env.OPENVIKING_HOOK_EVENT = event;
process.env.OPENVIKING_HOOK_SOURCE = clientId;

const stageName = host.stages[event] || "";
let cfg = loadAgentHookConfig(clientId);
const { log, logError } = createAgentLogger(clientId, event, cfg);

let emitted = false;
function emit(block) {
  if (emitted) return;
  emitted = true;
  const value = host.envelope(event, block || "");
  if (typeof value === "string") process.stdout.write(`${value}\n`);
  else if (value) process.stdout.write(`${JSON.stringify(value)}\n`);
}

const normalize = (input) => (host.normalizeInput ? host.normalizeInput(input) : input);

const contextBlock = (source, body) => (
  body ? `<openviking-context source="${source}">\n${body}\n</openviking-context>` : ""
);

const DEFAULT_IDLE_TTL_MS = 1_800_000;
const DEFAULT_COMMITTED_TTL_MS = 2_592_000_000;
const STALE_LOCK_MS = 60_000;

function positiveInt(raw, fallback) {
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : fallback;
}

const safePart = (value) => String(value || "unknown").replace(/[^A-Za-z0-9._-]/g, "-");

const hookStateRoot = () => process.env.OPENVIKING_HOOK_STATE_DIR || join(homedir(), ".openviking", "hook-state");

const lastTouchMs = (state, fallbackMs = 0) => Math.max(
  Number(state.lastActivityAt || 0),
  Number(state.promptAt || 0),
  Number(state.lastSessionStartAt || 0),
  Number(fallbackMs || 0),
);

function sessionSweepMeta(ctx, now) {
  const transcriptPath = ctx.input.transcriptPath || ctx.input.transcript_path;
  return {
    lastActivityAt: now,
    cwd: ctx.cwd,
    ...(transcriptPath ? { transcriptPath } : {}),
  };
}

async function tryAgentHookLock(file, callback) {
  const lock = `${file}.lock`;
  try {
    await mkdir(lock, { mode: 0o700 });
  } catch (error) {
    if (error?.code !== "EEXIST") return false;
    try {
      if (Date.now() - (await stat(lock)).mtimeMs <= STALE_LOCK_MS) return false;
      await rm(lock, { recursive: true, force: true });
      await mkdir(lock, { mode: 0o700 });
    } catch {
      return false;
    }
  }
  try {
    await callback();
    return true;
  } finally {
    await rm(lock, { recursive: true, force: true }).catch(() => {});
  }
}

function spawnDetachedSweep(excludeSessionId) {
  try {
    const child = spawn(process.execPath, [process.argv[1], "sweep", clientId, excludeSessionId || ""], {
      detached: true,
      stdio: "ignore",
      env: { ...process.env, OV_HOOK_WORKER: "1" },
    });
    child.unref();
  } catch (error) {
    logError("sweep_spawn", error);
  }
}

/**
 * Commit sessions left below `commitTurnThreshold` on hosts with no exit hook,
 * and retire stale cursor files so the state directory stays bounded.
 */
async function sweepIdleSessions(excludeSessionId = "") {
  const dir = join(hookStateRoot(), safePart(clientId));
  let entries;
  try {
    entries = await readdir(dir);
  } catch {
    return;
  }
  const now = Date.now();
  const idleTtlMs = positiveInt(process.env.OPENVIKING_IDLE_TTL_MS, DEFAULT_IDLE_TTL_MS);
  const committedTtlMs = positiveInt(process.env.OPENVIKING_COMMITTED_TTL_MS, DEFAULT_COMMITTED_TTL_MS);

  for (const entry of entries) {
    if (!entry.endsWith(".json")) continue;
    const nativeSessionId = entry.slice(0, -5);
    if (!nativeSessionId || nativeSessionId === excludeSessionId) continue;
    const file = join(dir, entry);
    let snapshotInfo;
    let snapshot;
    try {
      snapshotInfo = await stat(file);
      snapshot = JSON.parse(await readFile(file, "utf8"));
    } catch {
      continue;
    }
    if (!snapshot || typeof snapshot !== "object") continue;
    if (now - lastTouchMs(snapshot, snapshotInfo.mtimeMs) <= idleTtlMs) continue;

    await tryAgentHookLock(file, async () => {
      let freshInfo;
      let fresh;
      try {
        freshInfo = await stat(file);
        fresh = JSON.parse(await readFile(file, "utf8"));
      } catch {
        return;
      }
      if (!fresh || typeof fresh !== "object") return;
      const resolvedTranscript = fresh.transcriptPath
        ? (host.resolveTranscriptPath ? host.resolveTranscriptPath(fresh.transcriptPath) : fresh.transcriptPath)
        : "";
      let transcriptMtimeMs = 0;
      if (resolvedTranscript) {
        try {
          transcriptMtimeMs = (await stat(resolvedTranscript)).mtimeMs;
        } catch {}
      }
      // A long tool loop keeps writing to the transcript without re-entering
      // prompt hooks, so transcript mtime counts as liveness too.
      const touchMs = Math.max(lastTouchMs(fresh, freshInfo.mtimeMs), transcriptMtimeMs);
      const ageMs = now - touchMs;
      if (ageMs <= idleTtlMs) return;

      const sessionCwd = String(fresh.cwd || process.cwd());
      const sessionCfg = loadAgentHookConfig(clientId, sessionCwd);
      if (sessionCfg.enabled === false || !isCaptureEnabled(sessionCfg)) return;
      const payload = normalize({
        session_id: nativeSessionId,
        conversationId: nativeSessionId,
        cwd: sessionCwd,
        ...(fresh.transcriptPath ? { transcriptPath: fresh.transcriptPath } : {}),
      });
      if (shouldBypassAgent(sessionCfg, payload)) return;

      const sweptAt = Number(fresh.sweptAt || 0);
      const hasUncommitted = Number(fresh.capturedSinceCommit || 0) > 0;
      const needsCatchUp = Boolean(resolvedTranscript)
        && (sweptAt < touchMs || Boolean(fresh.transcriptUnreadable));
      if (hasUncommitted || needsCatchUp) {
        const agent = makeAgentFetchJSON(sessionCfg, sessionCwd);
        const sessionCtx = {
          cfg: sessionCfg,
          cwd: sessionCwd,
          input: payload,
          nativeSessionId,
          sessionId: deriveAgentSessionId(host.prefix, payload),
          stateFile: file,
          fetchJSON: withRequestBudget(
            agent.fetchJSON,
            host.requestBudgets?.["session-end"] ?? host.requestBudgets?.stop,
            sessionCfg.timeoutMs,
          ),
          get peerId() { return agent.effectivePeer.peerId; },
          log,
          logError,
        };
        const next = await host.capture(sessionCtx, fresh, "session-end");
        if (next) {
          fresh = { ...next, lastActivityAt: touchMs, sweptAt: now };
          await writeHookState(clientId, nativeSessionId, fresh);
        } else {
          fresh = { ...fresh, lastActivityAt: touchMs, sweptAt: now };
        }
      }

      if (Number(fresh.capturedSinceCommit || 0) > 0 || fresh.transcriptUnreadable) return;
      const hasCursor = Number.isInteger(fresh.lastStep) && fresh.lastStep >= 0;
      if (ageMs > (hasCursor ? committedTtlMs : idleTtlMs)) {
        await rm(file, { force: true }).catch(() => {});
        log("state_retire", { sessionId: nativeSessionId, hasCursor, ageMs });
      }
    });
  }
}

async function sessionStart(ctx) {
  let block = "";
  if (ctx.bypassed) {
    log("skip", { reason: "bypass" });
    await replayAgentPending(ctx.fetchJSON, log).catch((error) => logError("pending", error));
  } else {
    block = await withAgentHookLock(clientId, ctx.nativeSessionId, async () => {
      const state = await readHookState(clientId, ctx.nativeSessionId);
      const now = Date.now();
      if (now - Number(state.lastSessionStartAt || 0) < 2000) return "";
      await writeHookState(clientId, ctx.nativeSessionId, {
        ...state,
        lastSessionStartAt: now,
        ...(host.sweepsIdleSessions ? sessionSweepMeta(ctx, now) : {}),
      });
      await replayAgentPending(ctx.fetchJSON, log).catch((error) => logError("pending", error));
      if ((host.profileStage || "session-start") !== "session-start") return "";
      const profile = await buildAgentProfile(ctx.fetchJSON, ctx.cfg, ctx.cwd).catch((error) => {
        logError("profile", error);
        return null;
      });
      return contextBlock("session-start", profile);
    }) || "";
  }
  if (host.sweepsIdleSessions && isCaptureEnabled(ctx.cfg)) {
    if (ctx.cfg.writePathAsync) {
      ctx.emit(block);
      spawnDetachedSweep(ctx.nativeSessionId);
      return block;
    }
    await sweepIdleSessions(ctx.nativeSessionId);
  }
  return block;
}

async function promptSubmit(ctx) {
  const prompt = host.prompt(ctx.input);
  if (!prompt) return "";
  return withAgentHookLock(clientId, ctx.nativeSessionId, async () => {
    const state = await readHookState(clientId, ctx.nativeSessionId);
    const promptHash = stableHash(prompt);
    const now = Date.now();
    const { input } = ctx;
    const promptEventId = input.generation_id || input.request_id || input.message_id || input.prompt_id || "";
    const duplicateEvent = promptEventId
      ? state.promptEventId === promptEventId
      : state.promptHash === promptHash && now - Number(state.promptAt || 0) < 500;
    if (duplicateEvent) return "";
    const parts = [];
    let profileInjected = Boolean(state.profileInjected);
    if (host.profileStage === "first-prompt" && !profileInjected) {
      const profile = await buildAgentProfile(ctx.fetchJSON, ctx.cfg, ctx.cwd).catch((error) => {
        logError("profile", error);
        return null;
      });
      if (profile) parts.push(contextBlock("session-start", profile));
      profileInjected = Boolean(profile);
    }
    const recallBlock = state.promptHash === promptHash && state.recallBlock
      ? state.recallBlock
      : await recallForPrompt(ctx.fetchJSON, ctx.cfg, prompt, ctx.cwd, log, { sessionId: ctx.sessionId })
        .catch((error) => {
          logError("recall", error);
          return null;
        });
    if (recallBlock) parts.push(recallBlock);
    await writeHookState(clientId, ctx.nativeSessionId, {
      ...state,
      promptHash,
      promptEventId,
      promptAt: now,
      recallBlock,
      ...(host.profileStage === "first-prompt" ? { profileInjected } : {}),
      ...(host.tracksPendingPrompt ? { pendingPrompt: { prompt, hash: promptHash, at: now } } : {}),
      ...(host.sweepsIdleSessions ? sessionSweepMeta(ctx, now) : {}),
    });
    return parts.join("\n\n");
  });
}

async function capture(ctx) {
  if (host.capturesOnlyWhenEnabled && !isCaptureEnabled(ctx.cfg)) return "";
  // A terminal event that loses the lock to a slow Stop would drop the final
  // commit, so a detached one keeps waiting up to the stale-lock age.
  const terminal = host.detachesCapture && (event === "session-end" || event === "pre-compact");
  for (let attempt = 0; attempt < (terminal ? 12 : 1); attempt += 1) {
    const done = await withAgentHookLock(clientId, ctx.nativeSessionId, async (stateFile) => {
      const state = await readHookState(clientId, ctx.nativeSessionId);
      ctx.stateFile = stateFile;
      const next = await host.capture(ctx, state, event);
      if (next) await writeHookState(clientId, ctx.nativeSessionId, next);
      return true;
    });
    if (done) return "";
  }
  log("capture_lock_timeout");
  return "";
}

async function main() {
  if (event === "sweep") {
    if (host.sweepsIdleSessions && cfg.enabled !== false && isCaptureEnabled(cfg)) {
      await sweepIdleSessions(process.argv[4] || "");
    }
    return;
  }
  // The write path answers before it works, so the detach decision is taken
  // against this process's directory, before stdin is consumed.
  if (host.detachesCapture && stageName === "capture" && isCaptureEnabled(cfg)
    && await maybeDetach(cfg, { approve: () => emit() })) {
    return;
  }
  await runHookStage({
    clientId,
    input: { read: readHookStdin, tolerant: true },
    // These harnesses spell the session id in more ways than a payload key, so
    // the gate is given the same resolver the hook's own state files use.
    sessionId: (payload) => resolveNativeSessionId(normalize(payload)),
    gates: {
      enabled: (value) => value.enabled,
      bypass: (_cfg, stage) => (stageName === "start" && host.sweepsIdleSessions ? false : stage.bypassed),
    },
    envelope: emit,
    onSkip: (reason) => log("skip", { reason }),
  }, async ({ cfg: resolved, cwd, input, bypassed, emit: stageEmit }) => {
    cfg = resolved;
    const payload = normalize(input);
    if (!stageName) return "";
    const agent = makeAgentFetchJSON(cfg, cwd);
    const baseFetchJSON = agent.fetchJSON;
    const ctx = {
      cfg,
      cwd,
      input: payload,
      bypassed,
      emit: stageEmit,
      nativeSessionId: resolveNativeSessionId(payload),
      sessionId: deriveAgentSessionId(host.prefix, payload),
      fetchJSON: withRequestBudget(baseFetchJSON, host.requestBudgets?.[event], cfg.timeoutMs),
      // The peer the actor-peer header already carries, stamped into captured
      // messages because session writes read it from the body only.
      get peerId() { return agent.effectivePeer.peerId; },
      log,
      logError,
    };
    if (stageName === "start") return sessionStart(ctx);
    if (stageName === "prompt") return promptSubmit(ctx);
    return capture(ctx);
  });
}

main().catch((error) => {
  logError("uncaught", error);
  emit();
});
