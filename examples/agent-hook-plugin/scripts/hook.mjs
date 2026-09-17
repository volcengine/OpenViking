#!/usr/bin/env node

/** Shared lifecycle runner; host-specific parsing and envelopes live under hosts/. */

import { runAgentHook } from "../../memory-plugin-shared/lib/hook-runner.mjs";
import { HOSTS } from "../hosts/index.mjs";

// A detached writer re-enters this file with no arguments, so the first run's
// environment carries the event and client identity into the worker.
const event = process.argv[2] || process.env.OPENVIKING_HOOK_EVENT || "";
const clientId = process.argv[3] || process.env.OPENVIKING_HOOK_SOURCE || "";
runAgentHook({ clientId, event, host: HOSTS[clientId] }).catch((error) => {
  const value = HOSTS[clientId]?.envelope(event, "");
  if (value) process.stdout.write(`${JSON.stringify(value)}\n`);
  process.stderr.write(`openviking: ${error?.message || error}\n`);
});
