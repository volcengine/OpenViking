#!/usr/bin/env node

/**
 * stdio MCP proxy for Kimi Code.
 *
 * The host starts this as a local stdio server. It resolves its connection
 * through the same `loadConfig` the hooks use — not a second loader, not a
 * second credential chain — projects that into the shared MCP config, and hands
 * it to the shared proxy. `tools/list` is the server's own catalogue; this file
 * has no tool schema of its own.
 *
 * Two host facts shape it:
 *
 * - The process starts in the plugin root, which is not the user's project, so
 *   a peer derived from `cwd` would be wrong. `resolveMcpActorPeerId` decides,
 *   and without an explicit peer it reads wide and warns, as the shared layer
 *   documents.
 * - stdout belongs to the JSON-RPC transport. Every log goes to the shared
 *   logger, which writes files and stderr.
 */

import { realpathSync } from "node:fs";
import { resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";

import { createLogger } from "../scripts/shared/debug-log.mjs";
import { toMcpProxyConfig } from "../scripts/shared/mcp-proxy-config.mjs";
import { createOpenVikingMcpProxy } from "../scripts/shared/mcp-proxy-core.mjs";
import { loadConfig } from "../scripts/config.mjs";

/**
 * The proxy's configuration for one environment.
 *
 * Exported because the shared hook/MCP parity test drives it directly: a proxy
 * that resolved its own connection would be free to drift from the hooks, and
 * comparing what each would send is the only way to catch that.
 */
export function readProxyConfig(env = process.env) {
  return toMcpProxyConfig(loadConfig(process.cwd(), { env }), { env });
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
  createOpenVikingMcpProxy({
    readConfig: readProxyConfig,
    loggerFactory: (name, cfg) => createLogger(name, cfg),
  }).start();
}
