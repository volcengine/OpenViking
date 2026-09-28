#!/usr/bin/env node

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

if (process.argv[1] && fileURLToPath(import.meta.url) === resolvePath(process.argv[1])) {
  createOpenVikingMcpProxy({ readConfig: readProxyConfig, loggerFactory: createLogger }).start();
}
