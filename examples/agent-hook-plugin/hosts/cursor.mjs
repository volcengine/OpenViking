import { readFile } from "node:fs/promises";

import {
  addAgentMessages,
  commitAgentSession,
  stableHash,
} from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { filterCaptureTurns, isCaptureEnabled } from "../../memory-plugin-shared/lib/capture-utils.mjs";
import { denyCursorPermission, evaluateUriGuard } from "../../memory-plugin-shared/lib/uri-guard.mjs";
import { parseCursorTranscript } from "./cursor-transcript.mjs";

async function captureTranscript(ctx, state) {
  if (!isCaptureEnabled(ctx.cfg)) return { state, captured: 0 };
  const transcriptPath = ctx.input.transcript_path || ctx.input.transcriptPath;
  if (!transcriptPath) return { state, captured: 0 };
  let turns = [];
  try { turns = parseCursorTranscript(await readFile(transcriptPath, "utf8")); } catch { return { state, captured: 0 }; }
  const capturedHashes = new Set(Array.isArray(state.capturedHashes) ? state.capturedHashes : []);
  const toSend = [];
  for (const [index, turn] of turns.entries()) {
    // Cursor transcripts do not expose a stable message id. Include the
    // transcript position so two legitimate identical turns are retained,
    // while duplicate Hook executions over the same transcript still dedupe.
    // Hash the raw turn, not the filtered text, so raising captureMaxLength
    // never resends a turn the server already holds in truncated form.
    const hash = stableHash(index, turn.role, turn.content);
    if (capturedHashes.has(hash)) continue;
    const { kept, dropped } = filterCaptureTurns([turn], ctx.cfg);
    if (!kept.length) {
      ctx.log("capture_skip", dropped[0]);
      capturedHashes.add(hash);
      continue;
    }
    toSend.push({ hash, turn: kept[0] });
  }
  const result = await addAgentMessages(ctx.fetchJSON, ctx.sessionId, toSend.map((item) => item.turn));
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

export const cursor = {
  prefix: "cu-",
  stages: {
    sessionStart: "start",
    beforeSubmitPrompt: "prompt",
    stop: "capture",
    preCompact: "capture",
    sessionEnd: "capture",
  },
  envelope(event, block) {
    if (event === "beforeSubmitPrompt") {
      return block ? { continue: true, additional_context: block } : { continue: true };
    }
    if (event === "sessionStart" && block) return { additional_context: block };
    return {};
  },
  guard(input = {}) {
    const isShell = typeof input.command === "string";
    const decision = evaluateUriGuard(isShell ? "bash" : "read", input);
    if (!decision) return {};
    return denyCursorPermission(decision.reason, { agentMessage: isShell });
  },
  prompt: (input) => (typeof input.prompt === "string" ? input.prompt.trim() : ""),
  async capture(ctx, state, event) {
    const { state: next } = await captureTranscript(ctx, state);
    // Only a plain Stop waits for the threshold: a compaction or a session end
    // is the last chance this transcript has to reach the server.
    const shouldCommit = event !== "stop" || next.capturedSinceCommit >= ctx.cfg.commitTurnThreshold;
    if (shouldCommit) {
      const result = await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
      if (result.ok) next.capturedSinceCommit = 0;
    }
    return next;
  },
};
