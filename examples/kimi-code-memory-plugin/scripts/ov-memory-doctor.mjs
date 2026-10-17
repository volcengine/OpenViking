#!/usr/bin/env node

/**
 * Client-side diagnostics for the OpenViking Kimi Code memory plugin.
 *
 * Covers the plugin install (managed copy, manifest diagnostics, hook/MCP
 * wiring), the client config (which file won, is the JSON valid, what the key
 * claims) and the connection to the server (reachability, auth, tenant-data
 * access, /mcp), plus the runtime evidence the hooks leave in
 * ~/.openviking/kimicode-plugin-state. When the server runs on this machine
 * (loopback url) it also checks the port, plugin-only keys in ov.conf and
 * `GET /ready`. Provider-level validation stays with `openviking-server doctor`.
 *
 * Usage:
 *   node ov-memory-doctor.mjs [--json] [--offline] [--timeout <ms>] [--no-color]
 *
 * Exit code 1 when any check fails, 0 otherwise. Never prints a full api key.
 *
 * SCAFFOLD: mirrors examples/codex-memory-plugin/scripts/ov-memory-doctor.mjs.
 * TODO(DESIGN.md §11 step 6): fill in checkInstall (Kimi's managed plugin dir
 * and installed.json record), checkConfig and checkActivity for this harness;
 * the shared doctor-core provides the rest.
 */

import { realpathSync } from "node:fs";
import { resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";

import { loadConfig } from "./config.mjs";
import { getStateDir } from "./session-state.mjs";
import { runDoctor } from "./shared/doctor-core.mjs";

const PLUGIN_ROOT = resolvePath(fileURLToPath(import.meta.url), "..", "..");

const HOST = {
  harness: "kimicode",
  pluginRoot: PLUGIN_ROOT,
  cliName: "kimi",
  launcherHint: "Kimi Code",
  // Recall has a timeout of its own here, so it is the knob the prompt hook spends.
  timeoutBudgets: { UserPromptSubmit: "recallTimeoutMs", Stop: "captureTimeoutMs" },
  loadConfig,
  // TODO: install/config/activity checks for the Kimi managed-plugin layout.
  checkInstall: async () => [],
  checkConfig: async () => [],
  checkActivity: async () => [],
  resolveIdentity: (cfg) => ({ account: cfg.account, user: cfg.user }),
  extraResolved: (cfg) => ({ credentialSource: cfg.credentialSource, authMode: cfg.authMode }),
};

function isDirectRun() {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return resolvePath(process.argv[1]) === fileURLToPath(import.meta.url);
  }
}

if (isDirectRun()) {
  runDoctor(HOST).catch((err) => {
    console.error("ov-memory-doctor failed:", err?.stack || err?.message || err);
    process.exit(2);
  });
}
