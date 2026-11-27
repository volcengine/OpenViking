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
  stableHash,
  withAgentHookLock,
  writeHookState,
} from "../lib/agent-hook-runtime.mjs";
import { maybeDetach, readHookStdin } from "../lib/async-writer.mjs";
import { isCaptureEnabled } from "../lib/capture-utils.mjs";
import { formatSessionHistoryPointer } from "../lib/session-history-pointer.mjs";
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

// What `host.historyPointer[which]` makes of this event: "compact", "resume" or
// "". `inject` names the events whose own answer reaches the model, `defer` the
// ones that can only leave the note for the next prompt.
function historyPointerKind(ctx, which) {
  const signal = host.historyPointer?.[which];
  if (!signal || !ctx.cfg.sessionHistoryPointer || !isCaptureEnabled(ctx.cfg)) return "";
  return signal(event, ctx.input) || "";
}

// A resumed transcript still holds the note sent before, so a resume brings one
// only when none was sent or is pending; a pending compaction note stays.
function pointerFor(state, kind) {
  return kind === "resume" && (state.historyPointerSent || state.historyPointer) ? "" : kind;
}

function withDeferredPointer(state, kind) {
  const next = pointerFor(state, kind);
  return next ? { ...state, historyPointer: next } : state;
}

// On a host whose compaction drops the profile the session started with, the
// next prompt sends it again. Written with the deferred note, under its lock.
function profileDropped(ctx) {
  return Boolean(host.compactionDropsProfile)
    && ["inject", "defer"].some((which) => host.historyPointer?.[which]?.(event, ctx.input) === "compact");
}

function withCompaction(ctx, state) {
  return profileDropped(ctx) ? { ...state, profileStale: true } : state;
}

function historyPointer(ctx, kind) {
  if (!kind) return "";
  const { sessionDefault, tool } = host.historyPointer;
  return formatSessionHistoryPointer(ctx.sessionId, { event: kind, sessionDefault, tool });
}

async function sessionStart(ctx) {
  return withAgentHookLock(clientId, ctx.nativeSessionId, async () => {
    const state = await readHookState(clientId, ctx.nativeSessionId);
    const now = Date.now();
    const injected = pointerFor(state, historyPointerKind(ctx, "inject"));
    const pointer = historyPointer(ctx, injected);
    let next = withCompaction(ctx, withDeferredPointer(state, ctx.deferredPointer));
    if (injected) next = { ...next, historyPointerSent: true };
    // A resume and the compaction it runs can start within the window. A
    // SessionStart(compact) that reaches the model (Qoder) is the only profile
    // after that compaction, so it is never skipped.
    const answersCompaction = host.historyPointer?.inject?.(event, ctx.input) === "compact";
    if (now - Number(state.lastSessionStartAt || 0) < 2000 && !answersCompaction) {
      if (next !== state) await writeHookState(clientId, ctx.nativeSessionId, next);
      return pointer;
    }
    await writeHookState(clientId, ctx.nativeSessionId, { ...next, lastSessionStartAt: now });
    if (host.pendingReplayLimit && !process.env.OPENVIKING_PENDING_REPLAY_LIMIT) {
      process.env.OPENVIKING_PENDING_REPLAY_LIMIT = String(host.pendingReplayLimit);
    }
    await replayAgentPending(ctx.fetchJSON, log).catch((error) => logError("pending", error));
    if ((host.profileStage || "session-start") !== "session-start") return pointer;
    const profile = await buildAgentProfile(ctx.fetchJSON, ctx.cfg, ctx.cwd).catch((error) => {
      logError("profile", error);
      return null;
    });
    return [contextBlock("session-start", profile), pointer].filter(Boolean).join("\n\n");
  });
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
    // A host without a compaction hook (Antigravity) names its latest one here.
    const { latestCompaction } = input;
    let profileStale = Boolean(state.profileStale)
      || (latestCompaction !== undefined && latestCompaction !== state.profileCompaction);
    if (profileStale || (host.profileStage === "first-prompt" && !profileInjected)) {
      const profile = await buildAgentProfile(ctx.fetchJSON, ctx.cfg, ctx.cwd).catch((error) => {
        logError("profile", error);
        return null;
      });
      if (profile) {
        parts.push(contextBlock("session-start", profile));
        profileInjected = true;
        profileStale = false;
      }
    }
    const recallBlock = state.promptHash === promptHash && state.recallBlock
      ? state.recallBlock
      : await recallForPrompt(ctx.fetchJSON, ctx.cfg, prompt, ctx.cwd, log, { sessionId: ctx.sessionId })
        .catch((error) => {
          logError("recall", error);
          return null;
        });
    if (recallBlock) parts.push(recallBlock);
    const pointerSent = Boolean(state.historyPointer && ctx.cfg.sessionHistoryPointer && isCaptureEnabled(ctx.cfg));
    if (pointerSent) parts.push(historyPointer(ctx, state.historyPointer));
    await writeHookState(clientId, ctx.nativeSessionId, {
      ...state,
      historyPointer: undefined,
      ...(pointerSent ? { historyPointerSent: true } : {}),
      promptHash,
      promptEventId,
      promptAt: now,
      recallBlock,
      profileStale,
      ...(latestCompaction !== undefined ? { profileCompaction: latestCompaction } : {}),
      ...(host.profileStage === "first-prompt" ? { profileInjected } : {}),
      ...(host.tracksPendingPrompt ? { pendingPrompt: { prompt, hash: promptHash, at: now } } : {}),
    });
    return parts.join("\n\n");
  });
}

async function capture(ctx) {
  if (host.capturesOnlyWhenEnabled && !isCaptureEnabled(ctx.cfg)) {
    if (profileDropped(ctx)) {
      await withAgentHookLock(clientId, ctx.nativeSessionId, async () => {
        const state = await readHookState(clientId, ctx.nativeSessionId);
        await writeHookState(clientId, ctx.nativeSessionId, withCompaction(ctx, state));
      });
    }
    return "";
  }
  // A terminal event that loses the lock to a slow Stop would drop the final
  // commit, so a detached one keeps waiting up to the stale-lock age.
  const terminal = host.detachesCapture && (event === "session-end" || event === "pre-compact");
  for (let attempt = 0; attempt < (terminal ? 12 : 1); attempt += 1) {
    const done = await withAgentHookLock(clientId, ctx.nativeSessionId, async (stateFile) => {
      const read = await readHookState(clientId, ctx.nativeSessionId);
      const state = withCompaction(ctx, withDeferredPointer(read, ctx.deferredPointer));
      ctx.stateFile = stateFile;
      const next = await host.capture(ctx, state, event);
      if (next || state !== read) await writeHookState(clientId, ctx.nativeSessionId, next || state);
      return true;
    });
    if (done) return "";
  }
  log("capture_lock_timeout");
  return "";
}

async function main() {
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
    gates: { enabled: (value) => value.enabled },
    envelope: emit,
    onSkip: (reason) => log("skip", { reason }),
  }, async ({ cfg: resolved, cwd, input }) => {
    cfg = resolved;
    const payload = normalize(input);
    if (!stageName) return "";
    const agent = makeAgentFetchJSON(cfg, cwd);
    const baseFetchJSON = agent.fetchJSON;
    const ctx = {
      cfg,
      cwd,
      input: payload,
      nativeSessionId: resolveNativeSessionId(payload),
      sessionId: deriveAgentSessionId(host.prefix, payload),
      fetchJSON: withRequestBudget(baseFetchJSON, host.requestBudgets?.[event], cfg.timeoutMs),
      // The peer the actor-peer header already carries, stamped into captured
      // messages because session writes read it from the body only.
      get peerId() { return agent.effectivePeer.peerId; },
      log,
      logError,
    };
    ctx.deferredPointer = historyPointerKind(ctx, "defer");
    if (stageName === "fill") return host.fillHistorySession(payload, ctx.sessionId);
    if (stageName === "pointer") return historyPointer(ctx, historyPointerKind(ctx, "inject"));
    if (stageName === "start") return sessionStart(ctx);
    if (stageName === "prompt") return promptSubmit(ctx);
    return capture(ctx);
  });
}

main().catch((error) => {
  logError("uncaught", error);
  emit();
});
