import { fileURLToPath } from "node:url";

import { MCP_SERVER_NAME } from "./config.mjs";
import { forwardConnectionEnv } from "./shared/mcp-proxy-config.mjs";

/** The same stdio proxy every other OpenViking memory integration starts. */
export const PROXY_PATH = fileURLToPath(new URL("./servers/mcp-proxy.mjs", import.meta.url));

/**
 * Build the dsh-mcp-client config for the OpenViking stdio proxy.
 *
 * DSH scrubs credential-shaped names out of the environment a subprocess
 * inherits, and the Cordis patch is invisible to one, so the proxy cannot
 * resolve the connection the way this process did. It gets the answer instead:
 * the resolved connection and this session's peer, forwarded with the forced
 * `env` source so the child reads no file and lands on exactly this url, key,
 * identity, auth mode and peer — the empty ones included.
 */
export function buildMcpConfig(config) {
  // In DSH Desktop, process.execPath is Electron's executable rather than a
  // standalone Node binary. This tells Electron to run the proxy script as
  // Node instead of attempting to launch a second Desktop instance.
  const env = { ELECTRON_RUN_AS_NODE: "1", ...forwardConnectionEnv(config) };
  if (config.recallPeerScope) env.OPENVIKING_RECALL_PEER_SCOPE = config.recallPeerScope;
  if (config.timeoutMs) env.OPENVIKING_TIMEOUT_MS = String(config.timeoutMs);
  return {
    transport: "stdio",
    serverName: MCP_SERVER_NAME,
    command: process.execPath,
    args: [PROXY_PATH],
    env,
    toolCallTimeoutMs: config.mcpToolCallTimeoutMs,
  };
}
