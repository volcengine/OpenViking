#!/usr/bin/env node

/** Kimi-specific entry point; lifecycle ordering is shared with agent-hook hosts. */

import { runAgentHook } from "./shared/hook-runner.mjs";
import { kimicode } from "./kimicode-adapter.mjs";

const event = process.argv[2] || process.env.OPENVIKING_HOOK_EVENT || "";
runAgentHook({ clientId: "kimicode", event, host: kimicode }).catch((error) => {
  process.stderr.write(`openviking: ${error?.message || error}\n`);
});
