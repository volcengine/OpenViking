#!/usr/bin/env node

import { runUriGuardHook } from "../../memory-plugin-shared/lib/uri-guard.mjs";
import { HOSTS } from "../hosts/index.mjs";

// The deny envelope is the host's: Cursor answers its own permission shape,
// TRAE and ZCode the PreToolUse one.
export function evaluateHostUriGuard(clientId, input = {}) {
  return HOSTS[clientId] ? HOSTS[clientId].guard(input) : {};
}

const client = process.argv[2] || process.env.OPENVIKING_HOOK_SOURCE || "";
runUriGuardHook(import.meta.url, (input) => evaluateHostUriGuard(client, input));
