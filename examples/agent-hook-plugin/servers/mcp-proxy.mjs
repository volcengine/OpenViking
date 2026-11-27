#!/usr/bin/env node

import { realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve as resolvePath } from "node:path";

import { deriveAgentSessionId, loadAgentHookConfig } from "../lib/agent-hook-runtime.mjs";
import { createLogger } from "../lib/debug-log.mjs";
import { toMcpProxyConfig } from "../lib/mcp-proxy-config.mjs";
import { createOpenVikingMcpProxy } from "../lib/mcp-proxy-core.mjs";
import { HOSTS } from "../hosts/index.mjs";

export function readProxyConfig(env = process.env) {
  // The installer writes the client id into the MCP server's environment; it is
  // the only thing that tells this proxy which harness launched it. A hand-written
  // entry that names none resolves through the layers every harness shares rather
  // than borrowing another client's `plugin.<harness>` section.
  const requested = env.OPENVIKING_HOOK_SOURCE || "";
  const cfg = loadAgentHookConfig(HOSTS[requested] ? requested : "agent-hook", undefined, { env });
  return toMcpProxyConfig(cfg, { env });
}

/**
 * How a host that names the calling session in each tool call's `_meta` fills
 * `session_history`'s `session`: the OV id its hooks capture that session
 * into. Null for a host whose calls do not say.
 */
export function historySessionFor(clientId) {
  const host = HOSTS[clientId];
  if (!host?.historySessionFromMeta) return null;
  return (params) => {
    const id = host.historySessionFromMeta(params?._meta || {});
    return id ? deriveAgentSessionId(host.prefix, { session_id: id }) : "";
  };
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
    loggerFactory: createLogger,
    defaultHistorySession: historySessionFor(process.env.OPENVIKING_HOOK_SOURCE || ""),
  }).start();
}
