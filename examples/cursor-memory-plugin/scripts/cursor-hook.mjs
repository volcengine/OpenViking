#!/usr/bin/env node

import { readFile } from "node:fs/promises";
import { parseCursorTranscript } from "./lib/cursor-transcript.mjs";

import {
  addAgentMessage,
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
  resolveNativeSessionId,
  shouldBypassAgent,
  stableHash,
  withAgentHookLock,
  writeHookState,
} from "./shared/agent-hook-runtime.mjs";

const CLIENT_ID = "cursor";
const PREFIX = "cu-";
const eventName = process.argv[2] || "";
const cfg = loadAgentHookConfig(CLIENT_ID);
const { log, logError } = createAgentLogger(CLIENT_ID, eventName, cfg);

function output(value = {}) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

async function captureTranscript(input, state, sessionId) {
  if (!cfg.autoCapture) return { state, captured: 0 };
  const transcriptPath = input.transcript_path || input.transcriptPath;
  if (!transcriptPath) return { state, captured: 0 };
  let turns = [];
  try { turns = parseCursorTranscript(await readFile(transcriptPath, "utf8")); } catch { return { state, captured: 0 }; }
  const capturedHashes = new Set(Array.isArray(state.capturedHashes) ? state.capturedHashes : []);
  let captured = 0;
  for (const [index, turn] of turns.entries()) {
    // Cursor transcripts do not expose a stable message id. Include the
    // transcript position so two legitimate identical turns are retained,
    // while duplicate Hook executions over the same transcript still dedupe.
    const hash = stableHash(index, turn.role, turn.content);
    if (capturedHashes.has(hash)) continue;
    const result = await addAgentMessage(fetchJSON, sessionId, turn);
    if (result.ok || result.status === 0 || result.status >= 500) {
      capturedHashes.add(hash);
      captured += 1;
    }
  }
  return {
    captured,
    state: {
      ...state,
      capturedHashes: [...capturedHashes].slice(-1000),
      capturedSinceCommit: Number(state.capturedSinceCommit || 0) + captured,
    },
  };
}

const input = await readHookInput();
const nativeSessionId = resolveNativeSessionId(input);
const sessionId = deriveAgentSessionId(PREFIX, input);
const cwd = input.cwd || process.cwd();
const { fetchJSON } = makeAgentFetchJSON(cfg, cwd);

async function main() {
  if (!cfg.enabled || shouldBypassAgent(cfg, input)) {
    output(eventName === "beforeSubmitPrompt" ? { continue: true } : {});
    return;
  }

  let state = await readHookState(CLIENT_ID, nativeSessionId);
  if (eventName === "sessionStart") {
    const response = await withAgentHookLock(CLIENT_ID, nativeSessionId, async () => {
      state = await readHookState(CLIENT_ID, nativeSessionId);
      const now = Date.now();
      if (now - Number(state.lastSessionStartAt || 0) < 2000) return {};
      state = { ...state, lastSessionStartAt: now };
      await writeHookState(CLIENT_ID, nativeSessionId, state);
      await replayAgentPending(fetchJSON, log).catch((error) => logError("pending", error));
      const profile = await buildAgentProfile(fetchJSON, cfg, cwd).catch((error) => {
        logError("profile", error);
        return null;
      });
      return profile ? { additional_context: `<openviking-context source="session-start">\n${profile}\n</openviking-context>` } : {};
    });
    output(response || {});
    return;
  }

  if (eventName === "beforeSubmitPrompt") {
    await withAgentHookLock(CLIENT_ID, nativeSessionId, async () => {
      state = await readHookState(CLIENT_ID, nativeSessionId);
      const prompt = typeof input.prompt === "string" ? input.prompt.trim() : "";
      const promptHash = stableHash(prompt);
      if (prompt && (state.promptHash !== promptHash || !state.recallBlock || state.recallInjected)) {
        const recallBlock = await recallForPrompt(fetchJSON, cfg, prompt, cwd, log).catch((error) => {
          logError("recall", error);
          return null;
        });
        state = { ...state, promptHash, recallBlock, recallInjected: false, promptAt: Date.now() };
        await writeHookState(CLIENT_ID, nativeSessionId, state);
      }
    });
    output({ continue: true });
    return;
  }

  if (eventName === "postToolUse") {
    const response = await withAgentHookLock(CLIENT_ID, nativeSessionId, async () => {
      state = await readHookState(CLIENT_ID, nativeSessionId);
      if (!state.recallBlock || state.recallInjected) return {};
      state = { ...state, recallInjected: true };
      await writeHookState(CLIENT_ID, nativeSessionId, state);
      return { additional_context: state.recallBlock };
    });
    output(response || {});
    return;
  }

  if (["stop", "preCompact", "sessionEnd"].includes(eventName)) {
    await withAgentHookLock(CLIENT_ID, nativeSessionId, async () => {
      state = await readHookState(CLIENT_ID, nativeSessionId);
      const captured = await captureTranscript(input, state, sessionId);
      state = captured.state;
      const shouldCommit = eventName !== "stop" || state.capturedSinceCommit >= cfg.commitTurnThreshold;
      if (shouldCommit) {
        const result = await commitAgentSession(fetchJSON, sessionId);
        if (result.ok) state.capturedSinceCommit = 0;
      }
      await writeHookState(CLIENT_ID, nativeSessionId, state);
    });
    output({});
    return;
  }

  output({});
}

main().catch((error) => {
  logError("uncaught", error);
  output(eventName === "beforeSubmitPrompt" ? { continue: true } : {});
});
