#!/usr/bin/env node

import { realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve as resolvePath } from "node:path";

import { loadAgentHookConfig, resolveNativeSessionId } from "../lib/agent-hook-runtime.mjs";
import { createLogger } from "../lib/debug-log.mjs";
import { ovSessionId } from "../lib/flow/tool.mjs";
import { toMcpProxyConfig } from "../lib/mcp-proxy-config.mjs";
import { createOpenVikingMcpProxy } from "../lib/mcp-proxy-core.mjs";
import { loadHost } from "../hosts/index.mjs";

/**
 * The installer writes the client id into the MCP server's environment; it is
 * the only thing that tells this proxy which harness launched it. A hand-written
 * entry that names none resolves through the layers every harness shares rather
 * than borrowing another client's `plugin.<harness>` section.
 */
export function readProxyConfig(env = process.env, client = env.OPENVIKING_HOOK_SOURCE) {
  return toMcpProxyConfig(loadAgentHookConfig(client || "agent-hook", undefined, { env }), { env });
}

/**
 * How a host that names the calling session in each tool call's `_meta` fills
 * `session_history`'s `session`: the OV id its hooks capture that session
 * into. Null for a host whose calls do not say.
 */
export function historySessionFor(host) {
  if (!host?.mcpSession) return null;
  return (params) => {
    const id = host.mcpSession(params?._meta || {});
    return id ? ovSessionId(host, resolveNativeSessionId({ session_id: id })) : "";
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
  const host = await loadHost(process.env.OPENVIKING_HOOK_SOURCE || "");
  createOpenVikingMcpProxy({
    readConfig: (env) => readProxyConfig(env, host?.id || "agent-hook"),
    loggerFactory: createLogger,
    defaultHistorySession: historySessionFor(host),
  }).start();
}
