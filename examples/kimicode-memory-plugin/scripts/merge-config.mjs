#!/usr/bin/env node

/**
 * Idempotent installer helper for Kimi Code CLI.
 *
 * Usage: node merge-config.mjs <config.toml> <mcp.json> <pluginRoot> <nodeBin>
 *
 * Rewrites the `# >>> openviking kimicode integration` block in config.toml
 * and upserts mcpServers.openviking in mcp.json. Other hooks (orca, herdr)
 * are left untouched.
 */

import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

const BEGIN = "# >>> openviking kimicode integration";
const END = "# <<< openviking kimicode integration";

const [configPath, mcpPath, pluginRoot, nodeBin] = process.argv.slice(2);
if (!configPath || !mcpPath || !pluginRoot || !nodeBin) {
  console.error("usage: merge-config.mjs <config.toml> <mcp.json> <pluginRoot> <nodeBin>");
  process.exit(2);
}

function atomicWrite(file, next) {
  mkdirSync(dirname(file), { recursive: true });
  let previous = "";
  try {
    previous = readFileSync(file, "utf8");
  } catch {
    previous = "";
  }
  if (previous === next) return;
  if (previous) writeFileSync(`${file}.bak`, previous, { mode: 0o600 });
  const tmp = `${file}.${process.pid}.tmp`;
  writeFileSync(tmp, next, { mode: 0o600 });
  renameSync(tmp, file);
}

function hookCommand(script, extraArgs = "") {
  const quoted = `${nodeBin} '${pluginRoot}/scripts/${script}'${extraArgs ? ` ${extraArgs}` : ""}`;
  return quoted;
}

function hooksBlock() {
  return `${BEGIN}
[[hooks]]
event = "SessionStart"
command = "${hookCommand("kimicode-hook.mjs", "session-start")}"
timeout = 30

[[hooks]]
event = "UserPromptSubmit"
command = "${hookCommand("kimicode-hook.mjs", "user-prompt-submit")}"
timeout = 20

[[hooks]]
event = "PreToolUse"
matcher = "Read|Glob|Grep"
command = "${hookCommand("uri-guard.mjs")}"
timeout = 5

[[hooks]]
event = "Stop"
command = "${hookCommand("kimicode-hook.mjs", "stop")}"
timeout = 30

[[hooks]]
event = "PreCompact"
command = "${hookCommand("kimicode-hook.mjs", "pre-compact")}"
timeout = 30

[[hooks]]
event = "SessionEnd"
command = "${hookCommand("kimicode-hook.mjs", "session-end")}"
timeout = 30

[[hooks]]
event = "Interrupt"
command = "${hookCommand("kimicode-hook.mjs", "interrupt")}"
timeout = 10
${END}
`;
}

function stripBlock(text) {
  const begin = text.indexOf(BEGIN);
  if (begin < 0) return text.replace(/\s+$/u, "\n");
  const end = text.indexOf(END, begin);
  if (end < 0) return text.replace(/\s+$/u, "\n");
  const before = text.slice(0, begin).replace(/\s+$/u, "\n");
  const after = text.slice(end + END.length).replace(/^\s+/u, "\n");
  return `${before}${after}`.replace(/\n{3,}/g, "\n\n");
}

function mergeToml() {
  let current = "";
  try {
    current = readFileSync(configPath, "utf8");
  } catch {
    current = "";
  }
  const stripped = stripBlock(current).replace(/\s+$/u, "\n");
  const next = `${stripped ? `${stripped}\n` : ""}${hooksBlock()}`;
  atomicWrite(configPath, next.endsWith("\n") ? next : `${next}\n`);
}

function mergeMcp() {
  let parsed = {};
  try {
    parsed = JSON.parse(readFileSync(mcpPath, "utf8"));
  } catch {
    parsed = {};
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) parsed = {};
  if (!parsed.mcpServers || typeof parsed.mcpServers !== "object") parsed.mcpServers = {};
  parsed.mcpServers.openviking = {
    command: nodeBin,
    args: [`${pluginRoot}/servers/mcp-proxy.mjs`],
    env: { OPENVIKING_INTEGRATION_ID: "kimicode" },
  };
  atomicWrite(mcpPath, `${JSON.stringify(parsed, null, 2)}\n`);
}

mergeToml();
mergeMcp();
