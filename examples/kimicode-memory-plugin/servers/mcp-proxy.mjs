#!/usr/bin/env node

import { fileURLToPath } from "node:url";
import { resolve as resolvePath } from "node:path";

import { loadAgentHookConfig } from "../scripts/shared/agent-hook-runtime.mjs";
import { createLogger } from "../scripts/shared/debug-log.mjs";
import { toMcpProxyConfig } from "../scripts/shared/mcp-proxy-config.mjs";
import { createOpenVikingMcpProxy } from "../scripts/shared/mcp-proxy-core.mjs";

export function readProxyConfig(env = process.env) {
  const cfg = loadAgentHookConfig("kimicode", undefined, { env });
  return toMcpProxyConfig(cfg, { env });
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolvePath(process.argv[1])) {
  createOpenVikingMcpProxy({ readConfig: readProxyConfig, loggerFactory: createLogger }).start();
}
