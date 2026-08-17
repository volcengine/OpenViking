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
import { buildAgyTurns, latestAgyPrompt } from "./agy-turns.mjs";

const eventName = process.env.OPENVIKING_HOOK_EVENT || process.argv[2] || "";
const clientId = "agy";
const prefix = "ag-";

function applyAgyTuning(cfg) {
  const tuning = cfg.ovFile && typeof cfg.ovFile.agy === "object" ? cfg.ovFile.agy : {};
  const envPatterns = String(process.env.OPENVIKING_BYPASS_SESSION_PATTERNS || "").trim();
  cfg.bypassSessionPatterns = envPatterns
    ? envPatterns.split(",").map((item) => item.trim()).filter(Boolean)
    : (Array.isArray(tuning.bypassSessionPatterns)
        ? tuning.bypassSessionPatterns.filter((pattern) => typeof pattern === "string" && pattern.trim())
        : (cfg.bypassSessionPatterns || []));
  if (tuning.enabled === false) cfg.enabled = false;
  if (tuning.autoRecall === false) cfg.autoRecall = false;
  if (tuning.autoCapture === false) cfg.autoCapture = false;
  if (tuning.workspacePeer === false) cfg.workspacePeer = false;
  if (Number.isFinite(Number(tuning.scoreThreshold))) cfg.scoreThreshold = Number(tuning.scoreThreshold);
  if (Number.isFinite(Number(tuning.recallLimit))) cfg.recallLimit = Number(tuning.recallLimit);
  return cfg;
}

const cfg = applyAgyTuning(loadAgentHookConfig(clientId));
const { log, logError } = createAgentLogger(clientId, eventName, cfg);

function output(value = {}) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

const input = await readHookInput();
const nativeSessionId = resolveNativeSessionId(input);
const sessionId = deriveAgentSessionId(prefix, input);
const cwd = resolveAgentCwd(input);
const { fetchJSON } = makeAgentFetchJSON(cfg, cwd);

function injectBlocks(blocks) {
  const messages = (blocks || []).map((block) => block?.trim()).filter(Boolean);
  if (messages.length) return { injectSteps: messages.map((message) => ({ ephemeralMessage: message })) };
  return {};
}

async function main() {
  if (!cfg.enabled || shouldBypassAgent(cfg, input)) { output({}); return; }
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
    output(injectBlocks(profile ? [`<openviking-context source="session-start">\n${profile}\n</openviking-context>`] : []));
    return;
  }

  if (eventName === "pre-invocation") {
    await withAgentHookLock(clientId, nativeSessionId, async () => {
      state = await readHookState(clientId, nativeSessionId);
      const blocks = [];
      if (!state.bootstrapDone) {
        await replayAgentPending(fetchJSON, log).catch((error) => logError("pending", error));
        const profile = await buildAgentProfile(fetchJSON, cfg, cwd).catch((error) => {
          logError("profile", error);
          return null;
        });
        if (profile) blocks.push(`<openviking-context source="session-start">\n${profile}\n</openviking-context>`);
      }
      const prompt = latestAgyPrompt(input, state);
      if (prompt) {
        const promptHash = stableHash(prompt);
        const now = Date.now();
        const duplicate = state.promptHash === promptHash && now - Number(state.promptAt || 0) < 500;
        const recallBlock = duplicate
          ? state.recallBlock
          : await recallForPrompt(fetchJSON, cfg, prompt, cwd, log, { sessionId }).catch((error) => {
            logError("recall", error);
            return null;
          });
        if (!duplicate && recallBlock) blocks.push(recallBlock);
        await writeHookState(clientId, nativeSessionId, {
          ...state,
          bootstrapDone: true,
          promptHash,
          promptAt: now,
          recallBlock,
          pendingPrompt: { prompt, hash: promptHash, at: now },
        });
      } else {
        await writeHookState(clientId, nativeSessionId, { ...state, bootstrapDone: true });
      }
      output(injectBlocks(blocks));
    });
    return;
  }

  if (eventName === "stop") {
    if (!cfg.autoCapture) { output({}); return; }
    await withAgentHookLock(clientId, nativeSessionId, async () => {
      state = await readHookState(clientId, nativeSessionId);
      const hashes = new Set(Array.isArray(state.capturedHashes) ? state.capturedHashes : []);
      const toSend = [];
      for (const { stepKey, role, content } of buildAgyTurns(input, state)) {
        const hash = stableHash(stepKey, role, content);
        if (hashes.has(hash)) continue;
        toSend.push({ hash, turn: { role, content } });
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
        recallBlock: null,
        promptHash: null,
        promptAt: null,
      });
    });
    output({});
    return;
  }

  output({});
}

main().catch((error) => {
  logError("uncaught", error);
  output({});
});