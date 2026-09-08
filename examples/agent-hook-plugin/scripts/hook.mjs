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
} from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { maybeDetach, readHookStdin } from "../../memory-plugin-shared/lib/async-writer.mjs";
import { isCaptureEnabled } from "../../memory-plugin-shared/lib/capture-utils.mjs";
import { HOSTS } from "../hosts/index.mjs";

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
  if (value) process.stdout.write(`${JSON.stringify(value)}\n`);
}

const normalize = (input) => (host.normalizeInput ? host.normalizeInput(input) : input);

const contextBlock = (source, body) => (
  body ? `<openviking-context source="${source}">\n${body}\n</openviking-context>` : ""
);

async function sessionStart(ctx) {
  return withAgentHookLock(clientId, ctx.nativeSessionId, async () => {
    const state = await readHookState(clientId, ctx.nativeSessionId);
    const now = Date.now();
    if (now - Number(state.lastSessionStartAt || 0) < 2000) return "";
    await writeHookState(clientId, ctx.nativeSessionId, { ...state, lastSessionStartAt: now });
    await replayAgentPending(ctx.fetchJSON, log).catch((error) => logError("pending", error));
    const profile = await buildAgentProfile(ctx.fetchJSON, ctx.cfg, ctx.cwd).catch((error) => {
      logError("profile", error);
      return null;
    });
    return contextBlock("session-start", profile);
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
    const recallBlock = state.promptHash === promptHash && state.recallBlock
      ? state.recallBlock
      : await recallForPrompt(ctx.fetchJSON, ctx.cfg, prompt, ctx.cwd, log, { sessionId: ctx.sessionId })
        .catch((error) => {
          logError("recall", error);
          return null;
        });
    await writeHookState(clientId, ctx.nativeSessionId, {
      ...state,
      promptHash,
      promptEventId,
      promptAt: now,
      recallBlock,
      ...(host.tracksPendingPrompt ? { pendingPrompt: { prompt, hash: promptHash, at: now } } : {}),
    });
    return recallBlock || "";
  });
}

async function capture(ctx) {
  if (host.capturesOnlyWhenEnabled && !isCaptureEnabled(ctx.cfg)) return "";
  await withAgentHookLock(clientId, ctx.nativeSessionId, async () => {
    const state = await readHookState(clientId, ctx.nativeSessionId);
    const next = await host.capture(ctx, state, event);
    if (next) await writeHookState(clientId, ctx.nativeSessionId, next);
  });
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
    const ctx = {
      cfg,
      cwd,
      input: payload,
      nativeSessionId: resolveNativeSessionId(payload),
      sessionId: deriveAgentSessionId(host.prefix, payload),
      fetchJSON: makeAgentFetchJSON(cfg, cwd).fetchJSON,
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
