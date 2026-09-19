// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
import {
  buildAgentProfile,
  createAgentLogger,
  deriveAgentSessionId,
  loadAgentHookConfig,
  makeAgentFetchJSON,
  recallForPrompt,
  readHookState,
  replayAgentPending,
  resolveAgentCwd,
  resolveNativeSessionId,
  runHookStage,
  stableHash,
  withAgentHookLock,
  writeHookState,
} from "./agent-hook-runtime.mjs";
import { maybeDetach, readHookStdin } from "./async-writer.mjs";
import { isCaptureEnabled } from "./capture-utils.mjs";

const contextBlock = (source, body) => (
  body ? `<openviking-context source="${source}">\n${body}\n</openviking-context>` : ""
);

/**
 * Run the shared hook state machine with a host-specific adapter.
 *
 * Adapters own event names, prompt/transcript parsing, output envelopes, and
 * capture policy. This runner owns the ordering that must be identical across
 * hosts: read the payload, resolve the session cwd, apply gates, optionally
 * detach with the original stdin, then execute under the resolved config.
 */
export async function runAgentHook({ clientId, event, host }) {
  if (!host) {
    process.stderr.write(`openviking: no hook adapter for ${clientId || "?"}\n`);
    return;
  }

  process.env.OPENVIKING_HOOK_EVENT = event;
  process.env.OPENVIKING_HOOK_SOURCE = clientId;
  const stageName = host.stages[event] || "";
  let cfg = loadAgentHookConfig(clientId);
  let log = () => {};
  let logError = () => {};
  let emitted = false;

  const emit = (block) => {
    if (emitted) return;
    emitted = true;
    const value = host.envelope(event, block || "");
    if (value) process.stdout.write(`${JSON.stringify(value)}\n`);
  };
  const normalize = (input) => (host.normalizeInput ? host.normalizeInput(input) : input);
  const sessionStartDeadlineMs = host.sessionStartBudgetMs
    ? Date.now() + host.sessionStartBudgetMs
    : 0;

  async function sessionStart(ctx) {
    return withAgentHookLock(clientId, ctx.nativeSessionId, async () => {
      const state = await readHookState(clientId, ctx.nativeSessionId);
      const now = Date.now();
      if (now - Number(state.lastSessionStartAt || 0) < 2000) return "";
      const nextState = {
        ...state,
        lastSessionStartAt: now,
        ...(host.profileOnPrompt ? { profileInjected: false } : {}),
      };
      await writeHookState(clientId, ctx.nativeSessionId, nextState);
      await replayAgentPending(ctx.fetchJSON, log, { deadlineMs: sessionStartDeadlineMs })
        .catch((error) => logError("pending", error));
      if (host.profileOnSessionStart === false) return "";
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
      const parts = [];
      let profileInjected = Boolean(state.profileInjected);
      if (host.profileOnPrompt && !profileInjected) {
        const profile = await buildAgentProfile(ctx.fetchJSON, ctx.cfg, ctx.cwd).catch((error) => {
          logError("profile", error);
          return null;
        });
        if (profile) parts.push(contextBlock("session-start", profile));
        profileInjected = Boolean(profile);
      }
      if (recallBlock) parts.push(recallBlock);
      await writeHookState(clientId, ctx.nativeSessionId, {
        ...state,
        promptHash,
        promptEventId,
        promptAt: now,
        recallBlock,
        ...(host.profileOnPrompt ? { profileInjected } : {}),
        ...(host.tracksPendingPrompt ? { pendingPrompt: { prompt, hash: promptHash, at: now } } : {}),
      });
      return parts.join("\n\n");
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

  const raw = await readHookStdin();
  let initialInput = {};
  try {
    initialInput = JSON.parse(raw || "{}");
  } catch {
    initialInput = {};
  }
  if (!initialInput || typeof initialInput !== "object") initialInput = {};
  const normalizedInput = normalize(initialInput);
  const initialCwd = resolveAgentCwd(normalizedInput);
  cfg = loadAgentHookConfig(clientId, initialCwd);
  ({ log, logError } = createAgentLogger(clientId, event, cfg));
  const defaultTimeoutMs = host.defaultTimeoutMs
    ? host.defaultTimeoutMs({ cfg, event })
    : undefined;

  const shouldDetach = host.detachesCapture
    && stageName === "capture"
    && (!host.detachEvents || host.detachEvents.has(event))
    && isCaptureEnabled(cfg);
  if (shouldDetach
    && await maybeDetach(cfg, { approve: () => emit(), raw })) {
    return;
  }

  await runHookStage({
    clientId,
    loadConfig: (cwd) => loadAgentHookConfig(clientId, cwd),
    input: { read: async () => raw, tolerant: true },
    sessionId: (payload) => resolveNativeSessionId(normalize(payload)),
    gates: { enabled: (value) => value.enabled },
    envelope: emit,
    onSkip: (reason) => log("skip", { reason }),
  }, async ({ cfg: resolved, cwd, input }) => {
    cfg = resolved;
    ({ log, logError } = createAgentLogger(clientId, event, cfg));
    const payload = normalize(input);
    if (!stageName) return "";
    const ctx = {
      cfg,
      cwd,
      input: payload,
      nativeSessionId: resolveNativeSessionId(payload),
      sessionId: deriveAgentSessionId(host.prefix, payload),
      fetchJSON: makeAgentFetchJSON(cfg, cwd, { defaultTimeoutMs }).fetchJSON,
      log,
      logError,
    };
    if (stageName === "start") return sessionStart(ctx);
    if (stageName === "prompt") return promptSubmit(ctx);
    return capture(ctx);
  });
}
