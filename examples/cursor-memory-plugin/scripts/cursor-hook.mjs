#!/usr/bin/env node

import { readFile } from "node:fs/promises";
import { parseCursorTranscript } from "./cursor-transcript.mjs";

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

const CLIENT_ID = "cursor";
const PREFIX = "cu-";
const eventName = process.env.OPENVIKING_HOOK_EVENT || process.argv[2] || "";
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
  const toSend = [];
  for (const [index, turn] of turns.entries()) {
    // Cursor transcripts do not expose a stable message id. Include the
    // transcript position so two legitimate identical turns are retained,
    // while duplicate Hook executions over the same transcript still dedupe.
    const hash = stableHash(index, turn.role, turn.content);
    if (capturedHashes.has(hash)) continue;
    toSend.push({ hash, turn });
  }
  const result = await addAgentMessages(fetchJSON, sessionId, toSend.map((item) => item.turn));
  const captured = result.sent + result.queued;
  for (const item of toSend.slice(0, captured)) capturedHashes.add(item.hash);
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
const cwd = resolveAgentCwd(input);
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
    const response = await withAgentHookLock(CLIENT_ID, nativeSessionId, async () => {
      state = await readHookState(CLIENT_ID, nativeSessionId);
      const prompt = typeof input.prompt === "string" ? input.prompt.trim() : "";
      const promptHash = stableHash(prompt);
      if (!prompt) return { continue: true };
      const now = Date.now();
      const promptEventId = input.generation_id || input.request_id || input.message_id || input.prompt_id || "";
      const duplicateEvent = promptEventId
        ? state.promptEventId === promptEventId
        : state.promptHash === promptHash && now - Number(state.promptAt || 0) < 500;
      if (duplicateEvent) return { continue: true };
      if (state.promptHash !== promptHash || !state.recallBlock) {
        const recallBlock = await recallForPrompt(fetchJSON, cfg, prompt, cwd, log).catch((error) => {
          logError("recall", error);
          return null;
        });
        state = { ...state, promptHash, recallBlock };
      }
      state = { ...state, promptEventId, promptAt: now };
      await writeHookState(CLIENT_ID, nativeSessionId, state);
      return state.recallBlock
        ? { continue: true, additional_context: state.recallBlock }
        : { continue: true };
    });
    output(response || { continue: true });
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
