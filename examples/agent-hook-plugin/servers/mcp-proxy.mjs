#!/usr/bin/env node

import { realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve as resolvePath } from "node:path";

import { loadAgentHookConfig } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { createLogger } from "../../memory-plugin-shared/lib/debug-log.mjs";
import { toMcpProxyConfig } from "../../memory-plugin-shared/lib/mcp-proxy-config.mjs";
import { createOpenVikingMcpProxy } from "../../memory-plugin-shared/lib/mcp-proxy-core.mjs";
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

function isDirectRun() {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return resolvePath(process.argv[1]) === fileURLToPath(import.meta.url);
  }
}

if (isDirectRun()) {
  createOpenVikingMcpProxy({ readConfig: readProxyConfig, loggerFactory: createLogger }).start();
}
