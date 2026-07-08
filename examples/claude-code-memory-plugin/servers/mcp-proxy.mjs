#!/usr/bin/env node

/**
 * stdio -> streamable-HTTP MCP proxy for the OpenViking Claude Code plugin.
 *
 * Claude Code starts this process as a local stdio MCP server. The proxy reads
 * the same OpenViking credential sources as the lifecycle hooks, forwards
 * JSON-RPC requests to the server's /mcp endpoint, and keeps stdout
 * protocol-clean.
 */

import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";
import { loadConfig } from "../scripts/config.mjs";
import { createLogger } from "../scripts/debug-log.mjs";
import { createOpenVikingMcpProxy } from "../scripts/shared/mcp-proxy-core.mjs";

const DEFAULT_TIMEOUT_MS = 15000;

function trimSlash(value) {
  return String(value || "").replace(/\/+$/, "");
}

function normalizePath(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (raw === "~") return homedir();
  if (raw.startsWith("~/")) return resolvePath(join(homedir(), raw.slice(2)));
  return resolvePath(raw);
}

function uniq(values) {
  return [...new Set(values.filter(Boolean))];
}

function readProxyConfig() {
  const cfg = loadConfig();
  return {
    mcpUrl: `${trimSlash(cfg.baseUrl)}/mcp`,
    apiKey: cfg.apiKey || "",
    account: cfg.accountId || "",
    user: cfg.userId || "",
    peerId: cfg.peerId || "",
    timeoutMs: Math.max(1000, Number(cfg.timeoutMs) || DEFAULT_TIMEOUT_MS),
    debug: cfg.debug === true,
    debugLogPath: cfg.debugLogPath,
    credentialSource: cfg.configPath?.endsWith("ovcli.conf") ? "ovcli" : "auto",
    credentialPath: cfg.configPath || "",
    watchedPaths: uniq([
      cfg.configPath,
      normalizePath(process.env.OPENVIKING_CLI_CONFIG_FILE),
      normalizePath(process.env.OPENVIKING_CONFIG_FILE),
      join(homedir(), ".openviking", "ovcli.conf"),
      join(homedir(), ".openviking", "ov.conf"),
    ]),
  };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolvePath(process.argv[1])) {
  createOpenVikingMcpProxy({ readConfig: readProxyConfig, loggerFactory: createLogger }).start();
}
