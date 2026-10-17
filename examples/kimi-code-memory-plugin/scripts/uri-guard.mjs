#!/usr/bin/env node

/**
 * PreToolUse URI guard for Kimi Code (matcher: Read|Glob|Grep).
 *
 * `viking://` is a virtual URI: local file tools can never read it, so a call
 * with one is denied with a reason that points the model at the OpenViking
 * MCP tools. Kimi's deny envelope is
 * `hookSpecificOutput.permissionDecision` + `permissionDecisionReason`.
 *
 * The guard is deliberately narrow: it checks the path argument of the
 * matched file tools only. It never grows into a general command interceptor,
 * and shell commands that merely mention a viking:// URI as data are left
 * alone (they run normally).
 *
 * SCAFFOLD: mirrors examples/codex-memory-plugin/scripts/uri-guard.mjs.
 */

import { preToolUseOutput, runUriGuardHook } from "./shared/uri-guard.mjs";

export const evaluatePreToolUse = (input = {}) =>
  preToolUseOutput(input, {
    guarded: new Set(["read", "glob", "grep"]),
  });

runUriGuardHook(import.meta.url, evaluatePreToolUse);
