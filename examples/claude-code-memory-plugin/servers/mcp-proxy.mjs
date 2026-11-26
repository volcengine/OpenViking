#!/usr/bin/env node

/**
 * stdio -> streamable-HTTP MCP proxy for the OpenViking Claude Code plugin.
 *
 * Claude Code starts this process as a local stdio MCP server. The proxy
 * resolves its connection through the hooks' own `loadConfig()`, forwards
 * JSON-RPC requests to the server's /mcp endpoint, and keeps stdout
 * protocol-clean. A `session_history` call without a `session` gets the
 * current Claude Code session's (scripts/lib/session-binding.mjs).
 */

import { realpathSync } from "node:fs";
import { resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";
import { isPluginEnabled, loadConfig } from "../scripts/config.mjs";
import { createLogger } from "../scripts/debug-log.mjs";
import { currentOvSessionId } from "../scripts/lib/session-binding.mjs";
import { toMcpProxyConfig } from "../scripts/shared/mcp-proxy-config.mjs";
import { createOpenVikingMcpProxy } from "../scripts/shared/mcp-proxy-core.mjs";

export function readProxyConfig(env = process.env) {
  return toMcpProxyConfig(loadConfig(undefined, { env }), { env });
}

// A disabled plugin no longer records the session at SessionStart, so what it
// recorded earlier may name a session that /clear has since replaced.
export function currentHistorySession({ pid = process.ppid, startedAt = 0, env = process.env, connectionScope } = {}) {
  if (!isPluginEnabled()) return "";
  const cfg = loadConfig(undefined, { env });
  if (cfg.enabled === false) return "";
  return currentOvSessionId({ pid, startedAt, env, connectionScope: connectionScope ?? cfg.connectionScope });
}

function isDirectRun() {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return resolvePath(process.argv[1]) === fileURLToPath(import.meta.url);
  }
}

if (isDirectRun()) {
  const startedAt = Date.now();
  createOpenVikingMcpProxy({
    readConfig: readProxyConfig,
    loggerFactory: createLogger,
    defaultHistorySession: (_params, config) => currentHistorySession({ startedAt, connectionScope: config.connectionScope }),
  }).start();
}
