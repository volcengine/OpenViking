/** Official MCP client with pi's connection lifecycle and result conversion. */
import { Client, ProtocolError, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { buildOvHeaders } from "../shared/ov-http.mjs";
import { toPiResult } from "./mcp-result.mjs";

export const MCP_PROTOCOL_VERSION = "2025-06-18";
export const DEFAULT_TIMEOUT_MS = 15000;
export const DEFAULT_HANDSHAKE_BUDGET_MS = 5000;

// connect() also sends an initialized notification, which has no SDK timeout.
function waitFor(promise, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
    promise.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
  });
}

const errorMessage = (error) => (error.status ? "HTTP " + error.status + ": " : "") + error.message;

export function createMcpBridge({ readConfig, clientInfo = { name: "openviking-pi", version: "0.0.0" } }) {
  const state = { connected: false, closed: false, error: null, tools: [] };
  let connection = null;

  function open(cfg, budgetMs) {
    if (state.closed) throw new Error("OpenViking MCP client is closed");
    const headers = buildOvHeaders(cfg, { actorPeerId: cfg.peerId, extraHeaders: cfg.extraHeaders });
    const key = JSON.stringify([cfg.mcpUrl, headers]);
    if (connection?.key === key) return connection;

    const previous = connection;
    connection = null;
    void previous?.client.close();
    state.connected = false;
    const client = new Client(clientInfo, { supportedProtocolVersions: [MCP_PROTOCOL_VERSION] });
    const transport = new StreamableHTTPClientTransport(new URL(cfg.mcpUrl), { requestInit: { headers } });
    const current = { key, client, ready: null };
    connection = current;
    client.onclose = () => {
      if (connection === current) {
        connection = null;
        state.connected = false;
      }
    };
    const signal = AbortSignal.timeout(budgetMs);
    current.ready = waitFor((async () => {
      await client.connect(transport, { signal, timeout: budgetMs });
      const listed = await client.listTools({}, { signal, timeout: budgetMs });
      if (connection !== current || state.closed) throw new Error("OpenViking MCP connection closed during startup");
      state.tools = listed.tools;
      state.connected = true;
      state.error = null;
      return client;
    })(), signal).catch(async (error) => {
      if (connection === current) {
        connection = null;
        state.connected = false;
        state.error = errorMessage(error);
      }
      await client.close();
      throw error;
    });
    return current;
  }

  async function connect(budgetMs = DEFAULT_HANDSHAKE_BUDGET_MS) {
    try {
      await open(readConfig(), budgetMs).ready;
    } catch (error) {
      // MCP failure must not prevent REST recall and session capture.
      if (!state.connected) state.error = errorMessage(error);
    }
    return state;
  }

  async function callTool(name, args, { signal } = {}) {
    signal?.throwIfAborted();
    const cfg = readConfig();
    const timeout = cfg.timeoutMs || DEFAULT_TIMEOUT_MS;
    const deadline = AbortSignal.timeout(timeout);
    const callSignal = signal ? AbortSignal.any([signal, deadline]) : deadline;
    const current = open(cfg, Math.min(timeout, DEFAULT_HANDSHAKE_BUDGET_MS));
    let result;
    try {
      const client = await waitFor(current.ready, callSignal);
      result = await client.callTool({ name, arguments: args }, { signal: callSignal, timeout });
    } catch (error) {
      // Protocol/tool errors and user cancellation do not invalidate a connection.
      // A failed transport is replaced on the next invocation; never replay a call.
      if (!(error instanceof ProtocolError) && !signal?.aborted && connection === current) {
        connection = null;
        state.connected = false;
        state.error = errorMessage(error);
        await current.client.close();
      }
      throw error;
    }
    return toPiResult(name, result);
  }

  async function close() {
    state.closed = true;
    state.connected = false;
    const current = connection;
    connection = null;
    await current?.client.close();
  }

  return { connect, callTool, close, state };
}
