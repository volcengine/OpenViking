#!/usr/bin/env node

import {
  addAgentMessages,
  buildAgentProfile,
  commitAgentSession,
  createAgentLogger,
  deriveAgentSessionId,
  loadAgentHookConfig,
  makeAgentFetchJSON,
  readHookInput,
  readHookState,
  recallForPrompt,
  replayAgentPending,
  resolveAgentCwd,
  resolveNativeSessionId,
  shouldBypassAgent,
  stableHash,
  withAgentHookLock,
  writeHookState,
} from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { buildTraeCliTurns, resolveTraeCliPrompt } from "./trae-cli-turns.mjs";

const eventName = process.env.OPENVIKING_HOOK_EVENT || process.argv[2] || "";
const clientId = "trae-cli";
const prefix = "trcli-";
const cfg = loadAgentHookConfig(clientId);
const { log, logError } = createAgentLogger(clientId, eventName, cfg);

function output(value = {}) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

function emitLifecycleOutput(additionalContext = "") {
  const value = {};
  if (additionalContext) {
    value.hookSpecificOutput = {
      hookEventName: eventName === "session-start" ? "SessionStart" : "UserPromptSubmit",
      additionalContext,
    };
  }
  output(value);
}

const input = await readHookInput();
const nativeSessionId = resolveNativeSessionId(input);
const sessionId = deriveAgentSessionId(prefix, input);
const cwd = resolveAgentCwd(input);
const { fetchJSON } = makeAgentFetchJSON(cfg, cwd);

async function main() {
  if (!cfg.enabled || shouldBypassAgent(cfg, input)) { emitLifecycleOutput(); return; }
  let state = await readHookState(clientId, nativeSessionId);

  if (eventName === "session-start") {
    const profile = await withAgentHookLock(clientId, nativeSessionId, async () => {
      state = await readHookState(clientId, nativeSessionId);
      const now = Date.now();
      if (now - Number(state.lastSessionStartAt || 0) < 2000) return null;
      state = { ...state, lastSessionStartAt: now };
      await writeHookState(clientId, nativeSessionId, state);
      await replayAgentPending(fetchJSON, log).catch((error) => logError("pending", error));
      return buildAgentProfile(fetchJSON, cfg, cwd).catch((error) => {
        logError("profile", error);
        return null;
      });
    });
    emitLifecycleOutput(profile ? `<openviking-context source="session-start">\n${profile}\n</openviking-context>` : "");
    return;
  }

  if (eventName === "user-prompt-submit") {
    const prompt = resolveTraeCliPrompt(input);
    if (!prompt) { emitLifecycleOutput(); return; }
    const recallBlock = await withAgentHookLock(clientId, nativeSessionId, async () => {
      state = await readHookState(clientId, nativeSessionId);
      const promptHash = stableHash(prompt);
      const now = Date.now();
      const promptEventId = input.generation_id || input.request_id || input.message_id || input.prompt_id || "";
      const duplicateEvent = promptEventId
        ? state.promptEventId === promptEventId
        : state.promptHash === promptHash && now - Number(state.promptAt || 0) < 500;
      if (duplicateEvent) return null;
      const block = state.promptHash === promptHash && state.recallBlock
        ? state.recallBlock
        : await recallForPrompt(fetchJSON, cfg, prompt, cwd, log, { sessionId }).catch((error) => {
          logError("recall", error);
          return null;
        });
      await writeHookState(clientId, nativeSessionId, {
        ...state,
        promptHash,
        promptEventId,
        promptAt: now,
        recallBlock: block,
        pendingPrompt: { prompt, hash: promptHash, at: now },
      });
      return block;
    });
    emitLifecycleOutput(recallBlock || "");
    return;
  }

  if (eventName === "stop") {
    if (!cfg.autoCapture) { emitLifecycleOutput(); return; }
    await withAgentHookLock(clientId, nativeSessionId, async () => {
      state = await readHookState(clientId, nativeSessionId);
      const hashes = new Set(Array.isArray(state.capturedHashes) ? state.capturedHashes : []);
      const turnKey = state.pendingPrompt?.at || state.lastTurnKey || state.promptHash || "unknown-turn";
      const toSend = [];
      for (const turn of buildTraeCliTurns(input, state)) {
        const hash = stableHash(turnKey, turn.role, turn.content);
        if (hashes.has(hash)) continue;
        toSend.push({ hash, turn });
      }
      const result = await addAgentMessages(fetchJSON, sessionId, toSend.map((item) => item.turn));
      const captured = result.sent + result.queued;
      for (const item of toSend.slice(0, captured)) hashes.add(item.hash);
      let nextCount = Number(state.capturedSinceCommit || 0) + captured;
      if (captured > 0) {
        const committed = await commitAgentSession(fetchJSON, sessionId, log);
        if (committed.ok) nextCount = 0;
      }
      await writeHookState(clientId, nativeSessionId, {
        ...state,
        capturedHashes: [...hashes].slice(-1000),
        capturedSinceCommit: nextCount,
        pendingPrompt: null,
        lastTurnKey: turnKey,
      });
    });
    emitLifecycleOutput();
    return;
  }

  emitLifecycleOutput();
}

main().catch((error) => {
  logError("uncaught", error);
  emitLifecycleOutput();
});
