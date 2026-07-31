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

export function evaluateCursorUriGuard(input = {}) {
  const isShell = typeof input.command === "string";
  const toolName = isShell ? "bash" : "read";
  const decision = evaluateAgentUriGuard(toolName, input);
  if (!decision) return {};
  const output = {
    permission: "deny",
    user_message: decision.reason,
  };
  if (isShell) output.agent_message = decision.reason;
  return output;
}

const isEntrypoint = process.argv[1]
  && realpathSync(fileURLToPath(import.meta.url)) === realpathSync(process.argv[1]);
if (isEntrypoint) {
  const output = evaluateCursorUriGuard(readInput());
  if (Object.keys(output).length > 0) process.stdout.write(`${JSON.stringify(output)}\n`);
}
