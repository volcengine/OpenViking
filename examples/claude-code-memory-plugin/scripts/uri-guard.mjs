#!/usr/bin/env node

import { readFileSync } from "node:fs";
import { buildGuardMessage, findVikingUri, normalizeToolName } from "./shared/uri-guard.mjs";

const TOOL_HINTS = {
  read: {
    tool: "OpenViking MCP read",
    example: (uri) => `read(uris="${uri}")`,
  },
  glob: {
    tool: "OpenViking MCP glob or list",
    example: (uri, input = {}) => `glob(pattern="${String(input.pattern ?? "**/*").replaceAll('"', '\\"')}", uri="${uri}")`,
  },
  grep: {
    tool: "OpenViking MCP grep or search",
    example: (uri, input = {}) => `grep(uri="${uri}", pattern="${String(input.pattern ?? "").replaceAll('"', '\\"')}")`,
  },
};

export function evaluatePreToolUse(input = {}) {
  const toolName = normalizeToolName(input.tool_name ?? input.toolName ?? input.name ?? input.tool);
  const hint = TOOL_HINTS[toolName];
  if (!hint) return {};

  const toolInput = input.tool_input ?? input.toolInput ?? input.input ?? {};
  const uri = findVikingUri(toolInput);
  if (!uri) return {};

  return {
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: buildGuardMessage(uri, {
        tool: hint.tool,
        example: hint.example(uri, toolInput),
      }),
    },
  };
}

function main() {
  let payload = {};
  try {
    const raw = readFileSync(0, "utf8").trim();
    if (raw) payload = JSON.parse(raw);
  } catch {
    payload = {};
  }

  const out = evaluatePreToolUse(payload);
  if (Object.keys(out).length > 0) {
    process.stdout.write(`${JSON.stringify(out)}\n`);
  }
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  main();
}
