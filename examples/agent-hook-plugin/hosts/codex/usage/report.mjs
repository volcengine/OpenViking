#!/usr/bin/env node

import { loadAgentHookConfig } from "../../../lib/agent-hook-runtime.mjs";
import { usageEnabled, usageOutput } from "./settings.mjs";
import { runHook } from "./hook-io.mjs";
import { formatReport } from "./display.mjs";
import { pruneSessions, pruneTurns, readTurn, writeRecall } from "./state.mjs";
import { readTranscriptRecall } from "./transcript.mjs";

await runHook(async (input) => {
  const cfg = loadAgentHookConfig("codex", input.cwd || undefined);
  if (!usageEnabled(cfg)) return {};
  const sessionId = input.session_id;
  const turnId = input.turn_id;
  if (!sessionId || !turnId) return {};

  const recalled = await readTranscriptRecall(input.transcript_path, turnId);
  const turn = await readTurn(sessionId, turnId);
  if (recalled.length) {
    turn.recalled = recalled;
    await writeRecall(sessionId, turnId, recalled);
  }
  const message = formatReport(turn, cfg);
  if (!message) return {};

  await pruneTurns(sessionId, turnId);
  await pruneSessions(sessionId);
  return usageOutput(cfg) === "terminal" ? { systemMessage: message } : {};
}, "report");
