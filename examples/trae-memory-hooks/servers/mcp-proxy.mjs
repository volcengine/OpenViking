#!/usr/bin/env node

import { fileURLToPath } from "node:url";
import { resolve as resolvePath } from "node:path";

import { loadAgentHookConfig } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { createLogger } from "../../memory-plugin-shared/lib/debug-log.mjs";
import { createOpenVikingMcpProxy } from "../../memory-plugin-shared/lib/mcp-proxy-core.mjs";

function readConfig() {
  const clientId = process.env.OPENVIKING_HOOK_SOURCE === "trae-cn" ? "trae-cn" : "trae";
  const cfg = loadAgentHookConfig(clientId);
  return {
    mcpUrl: cfg.mcpUrl,
    apiKey: cfg.apiKey,
    account: cfg.account,
    user: cfg.user,
    peerId: cfg.peerId,
    timeoutMs: cfg.timeoutMs,
    debug: cfg.debug,
    debugLogPath: cfg.debugLogPath,
    credentialSource: cfg.credentialSource,
    credentialPath: cfg.cliPath || cfg.ovPath || "",
    watchedPaths: [cfg.cliPath, cfg.ovPath].filter(Boolean),
  };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolvePath(process.argv[1])) {
  createOpenVikingMcpProxy({ readConfig, loggerFactory: createLogger }).start();
}
