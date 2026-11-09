#!/usr/bin/env node

/**
 * The secret-exec runner: `node secret-run.mjs '<command>'`. The PreToolUse
 * hook writes this wrapper; the work is `runSecretExec` in the shared module.
 */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";

import { loadConfig } from "./config.mjs";
import { makeFetchJSON } from "./lib/ov-session.mjs";
import { posixShell, privacyResolver, runSecretExec } from "./shared/secret-exec.mjs";

process.exitCode = await runSecretExec(process.argv[2], {
  env: process.env,
  resolve: privacyResolver(makeFetchJSON(loadConfig())),
  spawn,
  shell: posixShell(process.env, existsSync),
  stdout: process.stdout,
  stderr: process.stderr,
  signals: process,
});
