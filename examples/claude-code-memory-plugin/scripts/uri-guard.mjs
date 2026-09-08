#!/usr/bin/env node

import {
  denyHookSpecificOutput,
  evaluateUriGuard,
  runUriGuardHook,
} from "./shared/uri-guard.mjs";

// Claude Code names the same MCP tools as the shared hints, so only the guarded
// set is host data: Bash is left alone, a viking:// URI inside a shell command
// reaches the model.
const GUARDED_TOOLS = new Set(["read", "glob", "grep"]);

export function evaluatePreToolUse(input = {}) {
  const toolName = input.tool_name ?? input.toolName ?? input.name ?? input.tool;
  const toolInput = input.tool_input ?? input.toolInput ?? input.input ?? {};
  const decision = evaluateUriGuard(toolName, toolInput, { guarded: GUARDED_TOOLS });
  return decision ? denyHookSpecificOutput(decision.reason) : {};
}

runUriGuardHook(import.meta.url, evaluatePreToolUse);
