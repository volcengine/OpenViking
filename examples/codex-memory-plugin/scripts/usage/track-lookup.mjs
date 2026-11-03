#!/usr/bin/env node

import { loadConfig } from "../config.mjs";
import { usageEnabled } from "./settings.mjs";
import { runHook } from "./hook-io.mjs";
import {
  classifyCall,
  toolResponseFailed,
  successfulResponseText,
  urisIn,
} from "./sources.mjs";
import { writeLookup } from "./state.mjs";

await runHook(async (input) => {
  if (!usageEnabled(loadConfig(input.cwd || undefined))) return {};
  const sessionId = input.session_id;
  const turnId = input.turn_id;
  if (!sessionId || !turnId) return {};

  const call = classifyCall(input.tool_name, input.tool_input || {});
  if (!call) return {};

  const wrapped = ["functions.exec", "exec"].includes(input.tool_name);
  const responseText = successfulResponseText(input.tool_response);
  const lookup = {
    query: call.query,
    opened: call.opened,
    found: call.query !== null
      ? urisIn(responseText).filter((uri) => !call.opened.includes(uri))
      : [],
    // Successful wrapped output remains usable even when another result failed.
    isError: toolResponseFailed(input.tool_response) && (!wrapped || !responseText),
  };
  await writeLookup(sessionId, turnId, input.tool_use_id, lookup);
  // Codex injects PostToolUse additionalContext as a developer message right after
  // this tool's output, which splits parallel function_call_outputs; record only.
  return {};
}, "track-lookup");
