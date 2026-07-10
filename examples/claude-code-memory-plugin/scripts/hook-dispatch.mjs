#!/usr/bin/env node

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const scriptsDir = dirname(fileURLToPath(import.meta.url));
const targetName = basename(process.argv[2] || "");
const allowedTargets = new Set([
  "session-start.mjs",
  "auto-recall.mjs",
  "skill-experience.mjs",
  "uri-guard.mjs",
  "auto-capture.mjs",
  "pre-compact.mjs",
  "session-end.mjs",
  "subagent-start.mjs",
  "subagent-stop.mjs",
]);

if (!allowedTargets.has(targetName)) {
  process.stderr.write(`Unsupported OpenViking hook target: ${targetName || "(empty)"}\n`);
  process.exit(2);
}

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const rawInput = Buffer.concat(chunks).toString();
let input = {};
try { input = rawInput.trim() ? JSON.parse(rawInput) : {}; } catch {}

// Cursor can import installed Claude Code plugins automatically. When the
// command-installed native Cursor integration is present, let it be the only
// OpenViking Hook source so recall and capture are not executed twice.
const cursorMarker = join(homedir(), ".openviking", "agent-integrations", "cursor", ".native-hooks");
if (input.cursor_version && existsSync(cursorMarker)) {
  process.stdout.write("{}\n");
  process.exit(0);
}

const child = spawn(process.execPath, [join(scriptsDir, targetName)], {
  env: process.env,
  stdio: ["pipe", "pipe", "pipe"],
});
child.stdout.pipe(process.stdout);
child.stderr.pipe(process.stderr);
child.stdin.end(rawInput);
child.on("error", (error) => {
  process.stderr.write(`${error?.stack || error}\n`);
  process.exit(1);
});
child.on("close", (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  else process.exit(code ?? 1);
});
