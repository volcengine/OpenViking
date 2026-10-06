#!/usr/bin/env node

import { usageEnabled, usageOutput } from "./settings.mjs";
import { runHook } from "./hook-io.mjs";
import { formatReport } from "./display.mjs";
import { pruneSessions, pruneTurns, readTurn, writeRecall } from "./state.mjs";
import { readTranscriptRecall } from "./transcript.mjs";

await runHook(async (input) => {
  if (!usageEnabled()) return {};
  const sessionId = input.session_id;
  const turnId = input.turn_id;
  if (!sessionId || !turnId) return {};

  const recalled = await readTranscriptRecall(input.transcript_path, turnId);
  const turn = await readTurn(sessionId, turnId);
  if (recalled.length) {
    turn.recalled = recalled;
    await writeRecall(sessionId, turnId, recalled);
  }
  const message = formatReport(turn);
  if (!message) return {};

  await pruneTurns(sessionId, turnId);
  await pruneSessions(sessionId);
  return usageOutput() === "terminal" ? { systemMessage: message } : {};
}, "report");
