#!/usr/bin/env node

import { readFileSync, realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  evaluateAgentUriGuard,
} from "../../memory-plugin-shared/lib/agent-uri-guard.mjs";

function readInput() {
  try {
    const raw = readFileSync(0, "utf8").trim();
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export function evaluateTraeUriGuard(input = {}) {
  const toolName = input.tool_name ?? input.toolName ?? input.name ?? input.tool;
  const toolInput = input.tool_input ?? input.toolInput ?? input.input ?? {};
  const decision = evaluateAgentUriGuard(toolName, toolInput);
  if (!decision) return {};
  return {
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: decision.reason,
    },
  };
}

const isEntrypoint = process.argv[1]
  && realpathSync(fileURLToPath(import.meta.url)) === realpathSync(process.argv[1]);
if (isEntrypoint) {
  const output = evaluateTraeUriGuard(readInput());
  if (Object.keys(output).length > 0) process.stdout.write(`${JSON.stringify(output)}\n`);
}
