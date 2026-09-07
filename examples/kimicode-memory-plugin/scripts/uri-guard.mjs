#!/usr/bin/env node

/**
 * URI guard for Kimi Code CLI (PreToolUse).
 *
 * Denies Read/Glob/Grep of viking:// URIs. Kimi Code accepts either exit 2
 * (stderr reason) or JSON permissionDecision. We emit the documented JSON
 * object and still exit 0: fail-open on parser mismatch, deny when parsed.
 */

import { readFileSync, realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { evaluateAgentUriGuard } from "./shared/agent-uri-guard.mjs";

function readInput() {
  try {
    const raw = readFileSync(0, "utf8").trim();
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export function evaluateKimicodeUriGuard(input = {}) {
  const toolName = input.tool_name ?? input.toolName ?? input.name ?? input.tool;
  const toolInput = input.tool_input ?? input.toolInput ?? input.input ?? {};
  const decision = evaluateAgentUriGuard(toolName, toolInput);
  if (!decision) return {};
  return {
    hookSpecificOutput: {
      permissionDecision: "deny",
      permissionDecisionReason: decision.reason,
    },
  };
}

const isEntrypoint =
  process.argv[1] &&
  realpathSync(fileURLToPath(import.meta.url)) === realpathSync(process.argv[1]);
if (isEntrypoint) {
  const output = evaluateKimicodeUriGuard(readInput());
  if (Object.keys(output).length > 0) {
    process.stdout.write(`${JSON.stringify(output)}\n`);
  }
}
