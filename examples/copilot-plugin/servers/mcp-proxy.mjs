#!/usr/bin/env node

// Optional stdio -> OpenViking /mcp bridge for GitHub Copilot.
//
// Most Copilot users should connect Copilot directly to OpenViking's HTTP /mcp
// endpoint using `type: "http"` (see configs/). This stdio proxy exists for the
// same two reasons the Codex / ZCode / Cursor plugins ship one:
//
//   1. You want credentials auto-resolved from ~/.openviking/ovcli.conf instead
//      of hard-coded in each MCP config (handy when you rotate keys).
//   2. You run OpenViking locally on http://127.0.0.1:1933 without a root API
//      key and want Copilot to talk to it over stdio without exposing HTTP.
//
// Copilot CLI and VSCode Copilot Chat both accept stdio servers (`type: "local"`
// in ~/.copilot/mcp-config.json, or a plain `command/args` entry in
// .vscode/mcp.json). See INSTALL.md.

import { fileURLToPath } from "node:url";
import { resolve as resolvePath } from "node:path";

import { loadAgentHookConfig } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { createLogger } from "../../memory-plugin-shared/lib/debug-log.mjs";
import { createOpenVikingMcpProxy } from "../../memory-plugin-shared/lib/mcp-proxy-core.mjs";

function readConfig() {
  const clientId = "copilot";
  const cfg = loadAgentHookConfig(clientId);
  return {
    mcpUrl: cfg.mcpUrl,
    apiKey: cfg.apiKey,
    account: cfg.account,
    user: cfg.user,
    peerId: cfg.peerId,
    userAgent: cfg.userAgent,
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
