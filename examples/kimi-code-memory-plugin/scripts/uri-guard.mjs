#!/usr/bin/env node

/**
 * PreToolUse URI guard for Kimi Code (matcher: Read|Glob|Grep).
 *
 * `viking://` is a virtual URI: local file tools can never read it, so a call
 * with one is denied with a reason that points the model at the OpenViking
 * MCP tools. Kimi's deny envelope is
 * `hookSpecificOutput.permissionDecision` + `permissionDecisionReason`.
 *
 * The scope is the manifest matcher (`Read|Glob|Grep`), not a list here: the
 * shared guard resolves the tool name against its own table, so passing a
 * narrower set would be a claim the code never reads. Shell commands that
 * merely mention a viking:// URI as data are left alone (they run normally).
 */

import { preToolUseOutput, runUriGuardHook } from "./shared/uri-guard.mjs";

export const evaluatePreToolUse = (input = {}) => preToolUseOutput(input);

runUriGuardHook(import.meta.url, evaluatePreToolUse);
