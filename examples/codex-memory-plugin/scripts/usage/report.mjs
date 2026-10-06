#!/usr/bin/env node

import { usageEnabled } from "./settings.mjs";
import { runHook } from "./hook-io.mjs";
import { consulted, expandedLines, summaryLine } from "./sources.mjs";
import { pruneSessions, pruneTurns, readTurn, writeRecall } from "./state.mjs";
import { readTranscriptRecall } from "./transcript.mjs";

function expandedView() {
  const value = String(process.env.OPENVIKING_USAGE_VIEW || "summary").trim().toLowerCase();
  return value === "expanded" || value === "full" || value === "details";
}

await runHook(async (input) => {
  if (!usageEnabled()) return {};
  const sessionId = input.session_id;
  const turnId = input.turn_id;
  if (!sessionId || !turnId) return {};

  const recalled = await readTranscriptRecall(input.transcript_path, turnId);
  await writeRecall(sessionId, turnId, recalled);
  await pruneTurns(sessionId);
  await pruneSessions();

  const turn = await readTurn(sessionId, turnId);
  const result = consulted(turn);
  if (!result.rows.length && !turn.lookups.length) return {};

  const message = expandedView()
    ? expandedLines(turn, result).join("\n")
    : summaryLine(result);
  return { systemMessage: message };
});
