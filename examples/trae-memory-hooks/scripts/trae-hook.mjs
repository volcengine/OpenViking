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
import { buildTraeTurns, cleanTraeText } from "./trae-turns.mjs";

const eventName = process.env.OPENVIKING_HOOK_EVENT || process.argv[2] || "";
const requestedSource = process.env.OPENVIKING_HOOK_SOURCE || process.argv[3];
const requestedClient = requestedSource === "trae-cn" ? "trae-cn" : "trae";
const prefix = requestedClient === "trae-cn" ? "trcn-" : "tr-";
const cfg = loadAgentHookConfig(requestedClient);
const { log, logError } = createAgentLogger(requestedClient, eventName, cfg);

function output(value = {}) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

function approve(additionalContext = "") {
  const value = { decision: "approve" };
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
  if (!cfg.enabled || shouldBypassAgent(cfg, input)) { approve(); return; }
  let state = await readHookState(requestedClient, nativeSessionId);

  if (eventName === "session-start") {
    const profile = await withAgentHookLock(requestedClient, nativeSessionId, async () => {
      state = await readHookState(requestedClient, nativeSessionId);
      const now = Date.now();
      if (now - Number(state.lastSessionStartAt || 0) < 2000) return null;
      state = { ...state, lastSessionStartAt: now };
      await writeHookState(requestedClient, nativeSessionId, state);
      await replayAgentPending(fetchJSON, log).catch((error) => logError("pending", error));
      return buildAgentProfile(fetchJSON, cfg, cwd).catch((error) => {
        logError("profile", error);
        return null;
      });
    });
    approve(profile ? `<openviking-context source="session-start">\n${profile}\n</openviking-context>` : "");
    return;
  }

  if (eventName === "user-prompt-submit") {
    const prompt = cleanTraeText(input.prompt);
    if (!prompt) { approve(); return; }
    const recallBlock = await withAgentHookLock(requestedClient, nativeSessionId, async () => {
      state = await readHookState(requestedClient, nativeSessionId);
      const promptHash = stableHash(prompt);
      const now = Date.now();
      const promptEventId = input.generation_id || input.request_id || input.message_id || input.prompt_id || "";
      const duplicateEvent = promptEventId
        ? state.promptEventId === promptEventId
        : state.promptHash === promptHash && now - Number(state.promptAt || 0) < 500;
      if (duplicateEvent) return null;
      const block = state.promptHash === promptHash && state.recallBlock
        ? state.recallBlock
        : await recallForPrompt(fetchJSON, cfg, prompt, cwd, log).catch((error) => {
          logError("recall", error);
          return null;
        });
      await writeHookState(requestedClient, nativeSessionId, {
        ...state,
        promptHash,
        promptEventId,
        promptAt: now,
        recallBlock: block,
        pendingPrompt: { prompt, hash: promptHash, at: now },
      });
      return block;
    });
    approve(recallBlock || "");
    return;
  }

  if (eventName === "stop") {
    if (!cfg.autoCapture) { approve(); return; }
    await withAgentHookLock(requestedClient, nativeSessionId, async () => {
      state = await readHookState(requestedClient, nativeSessionId);
      const hashes = new Set(Array.isArray(state.capturedHashes) ? state.capturedHashes : []);
      const turnKey = state.pendingPrompt?.at || state.lastTurnKey || state.promptHash || "unknown-turn";
      const toSend = [];
      for (const turn of buildTraeTurns(input, state)) {
        const hash = stableHash(turnKey, turn.role, turn.content);
        if (hashes.has(hash)) continue;
        toSend.push({ hash, turn });
      }
      const result = await addAgentMessages(fetchJSON, sessionId, toSend.map((item) => item.turn));
      const captured = result.sent + result.queued;
      for (const item of toSend.slice(0, captured)) hashes.add(item.hash);
      let nextCount = Number(state.capturedSinceCommit || 0) + captured;
      if (captured > 0) {
        const committed = await commitAgentSession(fetchJSON, sessionId);
        if (committed.ok) nextCount = 0;
      }
      await writeHookState(requestedClient, nativeSessionId, {
        ...state,
        capturedHashes: [...hashes].slice(-1000),
        capturedSinceCommit: nextCount,
        pendingPrompt: null,
        lastTurnKey: turnKey,
      });
    });
    approve();
    return;
  }

  approve();
}

main().catch((error) => {
  logError("uncaught", error);
  approve();
});
