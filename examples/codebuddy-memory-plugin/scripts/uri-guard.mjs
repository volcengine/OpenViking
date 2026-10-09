#!/usr/bin/env node

/**
 * PreToolUse URI guard for CodeBuddy.
 *
 * A `viking://` URI is an OpenViking virtual path, not a local file. Recall
 * injection hands the model such URIs, so a model that then tries to open one
 * with a file tool cannot succeed — and a write would leave a junk local file
 * named after the URI. Those calls are denied, with a reason that names the
 * OpenViking MCP tool to use instead and shows an example call. Writes and
 * edits aimed at a skill URI are redirected to `add_skill`, since skills must
 * be installed through that tool rather than written file-by-file.
 *
 * ⚠️ Deliberately deny-only, unlike the shared guard. The shared
 * `preToolUseOutput` also emits a *notice* for shell tools (`additionalContext`)
 * on the theory that a `viking://` URI in a command line is as often data as a
 * path. On CodeBuddy that half cannot work: the hook contract has no
 * `additionalContext` under `PreToolUse` (only `permissionDecision`), and a
 * `permissionDecisionReason` was measured not to reach the model. So a notice
 * would be a branch that always no-ops. `Bash` is therefore left out of the
 * matcher below as well — running a guard that can only return `{}` is pure
 * hook latency. See docs/HOST-CONTRACT.md.
 *
 * The guard runs on every matched tool call, so it stays as light as the shared
 * evaluator allows: no config load, no network, one stdin read.
 */

import { denyHookSpecificOutput, evaluateUriGuard, runUriGuardHook } from "./shared/uri-guard.mjs";

export const evaluatePreToolUse = (input = {}) => {
  const toolName = input.tool_name ?? input.toolName ?? input.tool ?? input.name;
  const toolInput = input.tool_input ?? input.toolInput ?? input.input ?? {};
  const denied = evaluateUriGuard(toolName, toolInput, {});
  return denied ? denyHookSpecificOutput(denied.reason) : {};
};

runUriGuardHook(import.meta.url, evaluatePreToolUse);
