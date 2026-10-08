import { readFile } from "node:fs/promises";

import {
  addAgentMessages,
  stableHash,
} from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { filterCaptureTurns, isCaptureEnabled } from "../../memory-plugin-shared/lib/capture-utils.mjs";

/** Capture unseen turns from a host-provided JSONL transcript. */
export async function captureTranscript(ctx, state, parseTranscript) {
  if (!isCaptureEnabled(ctx.cfg)) return { state, captured: 0 };
  const transcriptPath = ctx.input.transcript_path || ctx.input.transcriptPath;
  if (!transcriptPath) return { state, captured: 0 };
  let turns = [];
  try { turns = parseTranscript(await readFile(transcriptPath, "utf8")); } catch { return { state, captured: 0 }; }
  const capturedHashes = new Set(Array.isArray(state.capturedHashes) ? state.capturedHashes : []);
  const toSend = [];
  for (const [index, turn] of turns.entries()) {
    // These transcripts have no stable message id. Keep the position so
    // identical real turns survive while repeated Stop events still dedupe.
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
  const result = await addAgentMessages(
    ctx.fetchJSON,
    ctx.sessionId,
    toSend.map((item) => item.turn),
    ctx.peerId,
  );
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
