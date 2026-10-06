#!/usr/bin/env node

import { usageEnabled } from "./settings.mjs";
import { runHook } from "./hook-io.mjs";
import {
  classifyCall,
  toolResponseFailed,
  toolResponseText,
  urisIn,
} from "./sources.mjs";
import { writeLookup } from "./state.mjs";

await runHook(async (input) => {
  if (!usageEnabled()) return {};
  const sessionId = input.session_id;
  const turnId = input.turn_id;
  if (!sessionId || !turnId) return {};

  const call = classifyCall(input.tool_name, input.tool_input || {});
  if (!call) return {};

  const responseText = toolResponseText(input.tool_response);
  const lookup = {
    query: call.query,
    opened: call.opened,
    found: call.query !== null
      ? urisIn(responseText).filter((uri) => !call.opened.includes(uri))
      : [],
    isError: toolResponseFailed(input.tool_response),
  };
  await writeLookup(sessionId, turnId, input.tool_use_id, lookup);
  return {};
}, "track-lookup");
