#!/usr/bin/env node

/**
 * stdio -> streamable-HTTP MCP proxy for the OpenViking DSH bundle.
 *
 * DSH's MCP bridge starts this process as a local stdio MCP server. The proxy
 * reads the same OpenViking credential sources as the in-process runtime; the
 * bundle also forwards its resolved config through the child environment, so
 * values that came from the Cordis patch survive the process boundary.
 */

import { resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";
import { resolveConfig } from "../config.mjs";
import { resolveOpenVikingCredentials } from "../shared/credentials.mjs";
import { createLogger } from "../shared/debug-log.mjs";
import { buildMcpProxyConfig } from "../shared/mcp-proxy-config.mjs";
import { createOpenVikingMcpProxy } from "../shared/mcp-proxy-core.mjs";

export function readProxyConfig(env = process.env, cwd = process.cwd()) {
  const cfg = resolveConfig({}, env, cwd);
  const creds = resolveOpenVikingCredentials(env);
  return buildMcpProxyConfig({
    baseUrl: cfg.endpoint,
    apiKey: cfg.apiKey,
    account: cfg.account,
    user: cfg.user,
    peerId: cfg.peerId,
    userAgent: cfg.userAgent,
    timeoutMs: cfg.requestTimeoutMs,
    debug: Boolean(env.OV_DEBUG_LOG),
    debugLogPath: env.OV_DEBUG_LOG,
    credentialSource: creds.credentialSource,
    credentialPath: creds.cliPath || creds.ovPath,
    watchedPaths: [creds.cliPath, creds.ovPath, creds.cliPathCandidate],
    env,
  });
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolvePath(process.argv[1])) {
  createOpenVikingMcpProxy({ readConfig: readProxyConfig, loggerFactory: createLogger }).start();
}
