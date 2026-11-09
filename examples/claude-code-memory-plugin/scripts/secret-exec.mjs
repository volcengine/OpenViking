#!/usr/bin/env node

/**
 * PreToolUse hook for secret exec: wraps a Bash command that names a saved
 * credential in `secret-run.mjs`, and denies `privacy_resolve`. Off unless
 * `secretExec` is set. It never reaches the network or a value; on any error it
 * prints nothing, so the command keeps its references and fails without one.
 */

import { fileURLToPath } from "node:url";

import { loadConfig } from "./config.mjs";
import { secretExecPreToolUse } from "./shared/secret-exec.mjs";
import { runUriGuardHook } from "./shared/uri-guard.mjs";

const WRAPPER = {
  node: process.execPath,
  runner: fileURLToPath(new URL("./secret-run.mjs", import.meta.url)),
};

export function evaluatePreToolUse(input = {}) {
  try {
    return secretExecPreToolUse(input, loadConfig(input.cwd), WRAPPER) ?? {};
  } catch {
    return {};
  }
}

runUriGuardHook(import.meta.url, evaluatePreToolUse);
