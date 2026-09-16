#!/usr/bin/env node

import { fileURLToPath } from "node:url";
import { resolve as resolvePath } from "node:path";

import { loadAgentHookConfig } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { createLogger } from "../../memory-plugin-shared/lib/debug-log.mjs";
import { buildMcpProxyConfig, resolveMcpActorPeerId } from "../../memory-plugin-shared/lib/mcp-proxy-config.mjs";
import { createOpenVikingMcpProxy } from "../../memory-plugin-shared/lib/mcp-proxy-core.mjs";
import { HOSTS } from "../hosts/index.mjs";

function readConfig() {
  // The installer writes the client id into the MCP server's environment; it is
  // the only thing that tells this proxy which harness launched it. A hand-written
  // entry that names none resolves through the layers every harness shares rather
  // than borrowing another client's `plugin.<harness>` section.
  const requested = process.env.OPENVIKING_HOOK_SOURCE || "";
  const cfg = loadAgentHookConfig(HOSTS[requested] ? requested : "agent-hook");
  return buildMcpProxyConfig({
    mcpUrl: cfg.mcpUrl,
    apiKey: cfg.apiKey,
    account: cfg.account,
    user: cfg.user,
    sendIdentityHeaders: cfg.sendIdentityHeaders,
    peerId: resolveMcpActorPeerId(cfg),
    userAgent: cfg.userAgent,
    timeoutMs: cfg.timeoutMs,
    debug: cfg.debug,
    debugLogPath: cfg.debugLogPath,
    credentialSource: cfg.credentialSource,
    credentialPath: cfg.cliPath || cfg.ovPath || "",
    watchedPaths: [cfg.cliPath, cfg.ovPath],
  });
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolvePath(process.argv[1])) {
  createOpenVikingMcpProxy({ readConfig, loggerFactory: createLogger }).start();
}
