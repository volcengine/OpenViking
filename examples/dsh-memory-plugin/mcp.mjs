import { fileURLToPath } from "node:url";
import * as mcpClient from "@deepseek-ai/dsh-mcp-client";

import { MCP_SERVER_NAME } from "./config.mjs";

/** The same stdio proxy every other OpenViking memory integration starts. */
export const PROXY_PATH = fileURLToPath(new URL("./servers/mcp-proxy.mjs", import.meta.url));

/**
 * Build the dsh-mcp-client config for the OpenViking stdio proxy.
 *
 * The bundle's own credential resolution (`OPENVIKING_*` → `ovcli.conf` →
 * `ov.conf`, plus anything set in the Cordis patch) is forwarded through the
 * child environment: DSH scrubs credential-shaped names out of the inherited
 * env, and the patch is invisible to a subprocess, so values the runtime
 * already resolved have to be passed explicitly.
 */
export function buildMcpConfig(config) {
  // In DSH Desktop, process.execPath is Electron's executable rather than a
  // standalone Node binary. This tells Electron to run the proxy script as
  // Node instead of attempting to launch a second Desktop instance.
  const env = { ELECTRON_RUN_AS_NODE: "1" };
  if (config.endpoint) env.OPENVIKING_URL = config.endpoint;
  if (config.apiKey) env.OPENVIKING_API_KEY = config.apiKey;
  if (config.account) env.OPENVIKING_ACCOUNT = config.account;
  if (config.user) env.OPENVIKING_USER = config.user;
  if (config.peerId) env.OPENVIKING_PEER_ID = config.peerId;
  return {
    transport: "stdio",
    serverName: MCP_SERVER_NAME,
    command: process.execPath,
    args: [PROXY_PATH],
    env,
    toolCallTimeoutMs: config.mcpToolCallTimeoutMs,
  };
}

/**
 * Mount the tool surface. The bridge ships with dsh itself, so it resolves
 * from the host installation and never needs a separate install step.
 * Startup failure is contained: recall, capture, and commit keep working
 * against a server whose MCP endpoint is unreachable.
 */
export function mountOpenVikingMcp(ctx, config) {
  return ctx.plugin(mcpClient, buildMcpConfig(config));
}
