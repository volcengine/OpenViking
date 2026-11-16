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
 * the resolved connection, diagnostics, and this session's peer, forwarded
 * with the forced `env` source so the child reads no file and lands on exactly
 * these values — the empty ones included.
 */
export function buildMcpConfig(config) {
  // In DSH Desktop, process.execPath is Electron's executable rather than a
  // standalone Node binary. This tells Electron to run the proxy script as
  // Node instead of attempting to launch a second Desktop instance.
  const env = {
    ELECTRON_RUN_AS_NODE: "1",
    ...forwardConnectionEnv(config),
    OPENVIKING_DEBUG: config.debug ? "1" : "0",
    OPENVIKING_DEBUG_LOG: config.debugLogPath || "",
  };
  if (config.recallPeerScope) env.OPENVIKING_RECALL_PEER_SCOPE = config.recallPeerScope;
  if (config.timeoutMs) env.OPENVIKING_TIMEOUT_MS = String(config.timeoutMs);
  return {
    transport: "stdio",
    serverName: MCP_SERVER_NAME,
    command: process.execPath,
    args: [PROXY_PATH],
    env,
    toolCallTimeoutMs: config.mcpToolCallTimeoutMs,
    // OpenViking needs the better part of a minute to come up: the models warm
    // up first, and only then does the listener bind. The proxy, by contrast,
    // starts instantly and fails fast while the HTTP endpoint is unreachable,
    // so the bridge burns its default ten attempts — about 150 s — before the
    // server has had a chance to answer. That budget is not just too short,
    // it is final: once spent, the bridge unregisters every tool and never
    // schedules another attempt, and the memory surface stays dark until the
    // host restarts. Give recovery a window wide enough to outlast a restart.
    reconnect: {
      enabled: true,
      initialDelayMs: 1000,
      maxDelayMs: 30000,
      maxAttempts: 60,
    },
  };
}
