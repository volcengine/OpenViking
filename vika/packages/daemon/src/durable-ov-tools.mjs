// SPDX-License-Identifier: AGPL-3.0

// The durable runtime's OpenViking tool source: the model-facing tool surface
// of the OpenViking connection (CONTEXT.md's "agent-side skill/MCP tools"),
// one instance per host process. Connects to the server's `/mcp` endpoint
// through @earendil-works/pi-mcp and turns its `tools/list` catalogue into
// pi-durable tool registrations, the way the official pi extension does
// (examples/pi-coding-agent-extension: one tool per server descriptor, named
// openviking_ + the server's own name, schema passed through untouched).
// This module owns only the catalogue and its connection lifecycle: the
// session/context pipeline (journal sync, session commit, fresh-session
// history) stays daemon-side in ov-session-context.mjs, and the host owns
// when to connect and how failures land.
//
// Identity comes from the same place the Core's session transport reads it:
// OPENVIKING_CLI_CONFIG_FILE (the instance's own config, actor_peer_id = the
// instance id) or OPENVIKING_URL for a local dev-mode server. An explicit
// config that is missing or invalid is never replaced by another identity.

import fs from 'node:fs';
import { Type } from '@earendil-works/pi-ai';
import { McpClient, McpError, StreamableHttpTransport, toLlmContent } from '@earendil-works/pi-mcp';
import { OVConnection, readOVConfig } from './ov-connection.mjs';

const TOOL_NAME_PREFIX = 'openviking_';
// The official extension's budgets: the whole handshake (connect + tools/list)
// may not block startup, and one call is bounded on its own.
export const OV_HANDSHAKE_BUDGET_MS = 5_000;
export const OV_CALL_TIMEOUT_MS = 15_000;
export const OV_RETRY_INTERVAL_MS = 60_000;

function packageVersion() {
  try {
    const manifest = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
    return String(manifest.version ?? '');
  } catch {
    return '';
  }
}

const CLIENT_INFO = { name: 'vika-durable', version: packageVersion() };

function trimmed(value) {
  return typeof value === 'string' ? value.trim() : '';
}

// Credentials for the MCP tools, resolved the way the deployment pins them.
// OPENVIKING_CLI_CONFIG_FILE is the instance config (url, api_key, account,
// user, actor_peer_id); a bare OPENVIKING_URL covers a local dev-mode server.
// Nothing else is consulted: a Vika instance's OpenViking identity is its own.
export function resolveOvConfig(environment = process.env) {
  const configPath = trimmed(environment.OPENVIKING_CLI_CONFIG_FILE);
  if (configPath) {
    try {
      return { config: readOVConfig(configPath) };
    } catch {
      return { config: null, error: 'the OpenViking configuration file is missing or invalid' };
    }
  }
  const url = trimmed(environment.OPENVIKING_URL);
  if (!url) return {};
  const config = { url };
  const apiKey = trimmed(environment.OPENVIKING_API_KEY) || trimmed(environment.OPENVIKING_BEARER_TOKEN);
  if (apiKey) config.api_key = apiKey;
  const account = trimmed(environment.OPENVIKING_ACCOUNT);
  if (account) config.account = account;
  const user = trimmed(environment.OPENVIKING_USER);
  if (user) config.user = user;
  return { config };
}

function actorPeer(config, environment) {
  return trimmed(environment.OPENVIKING_PEER_ID)
    || trimmed(config?.actor_peer_id) || trimmed(config?.peer_id);
}

// The MCP endpoint and its headers: the daemon's own OVConnection identity
// rules (URL validation included) without its JSON content type, plus the
// actor peer the instance is pinned to, exactly as the official extension
// sends it.
export function mcpEndpoint(config, environment = process.env) {
  const connection = new OVConnection(config);
  const { 'Content-Type': _json, ...headers } = connection.headers;
  const peer = actorPeer(config, environment);
  if (peer) headers['X-OpenViking-Actor-Peer'] = peer;
  return { url: `${connection.url}/mcp`, headers };
}

// The model-facing name for one server tool. Providers allow at most 64
// characters of [A-Za-z0-9_-].
export function ovToolName(name) {
  return `${TOOL_NAME_PREFIX}${String(name)}`.replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 64);
}

// One MCP server tool as a pi-durable tool registration. The server's own
// schema is passed through untouched — the Harness validates arguments before
// execute — and a tool the server marks read-only may rerun after a crash
// (replay: safe); write tools keep the default and never rerun.
export function toDurableTool(source, tool) {
  const serverName = String(tool?.name ?? '');
  const schema = tool?.inputSchema && typeof tool.inputSchema === 'object'
    ? tool.inputSchema
    : { type: 'object', properties: {} };
  return {
    name: ovToolName(serverName),
    description: String(tool?.description ?? '').trim() || String(tool?.title ?? '') || ovToolName(serverName),
    parameters: Type.Unsafe({ ...schema, type: 'object', properties: schema.properties ?? {} }),
    ...(tool?.annotations?.readOnlyHint === true ? { replay: 'safe' } : {}),
    execute: async (args) => {
      const result = await source.callTool(serverName, args ?? {});
      return { output: toLlmContent(result), isError: result.isError === true };
    },
  };
}

// The whole catalogue as tool registrations, in server order. A name that
// sanitizes onto an earlier one is skipped; the caller compares counts to
// notice and log the collision.
export function toDurableTools(source, tools) {
  const seen = new Set();
  const registrations = [];
  for (const tool of tools ?? []) {
    if (!tool?.name) continue;
    const registration = toDurableTool(source, tool);
    if (seen.has(registration.name)) continue;
    seen.add(registration.name);
    registrations.push(registration);
  }
  return registrations;
}

// The tool source's connection to the OpenViking MCP endpoint. `readConfig`
// is consulted before every connect and call, so rotated credentials apply
// without a host restart; a config whose endpoint or headers changed replaces
// the connection in place. A transport-level failure (HTTP error, timeout,
// closed stream) drops it and the next call rebuilds; a server-side JSON-RPC
// error does not, and no call is ever automatically replayed.
export class OVToolSource {
  constructor({
    readConfig,
    environment = process.env,
    clientInfo = CLIENT_INFO,
    handshakeMs = OV_HANDSHAKE_BUDGET_MS,
    callTimeoutMs = OV_CALL_TIMEOUT_MS,
    fetch: fetchImpl,
  } = {}) {
    if (typeof readConfig !== 'function') throw new Error('OVToolSource requires readConfig');
    this.readConfig = readConfig;
    this.environment = environment;
    this.clientInfo = clientInfo;
    this.handshakeMs = handshakeMs;
    this.callTimeoutMs = callTimeoutMs;
    this.fetchImpl = fetchImpl;
    this.connection = null;
    this.closed = false;
  }

  open(config) {
    if (this.closed) throw new Error('OpenViking MCP client is closed');
    const endpoint = mcpEndpoint(config, this.environment);
    const key = JSON.stringify([endpoint.url, endpoint.headers]);
    if (this.connection?.key === key) return this.connection;

    const previous = this.connection;
    this.connection = null;
    void previous?.client.close().catch(() => {});
    const client = new McpClient({ ...this.clientInfo, requestTimeoutMs: this.handshakeMs });
    const transport = new StreamableHttpTransport({
      url: endpoint.url,
      headers: endpoint.headers,
      openGetStream: false,
      ...(this.fetchImpl ? { fetch: this.fetchImpl } : {}),
    });
    const current = { key, client, ready: null };
    this.connection = current;
    client.onClose(() => {
      if (this.connection === current) this.connection = null;
    });
    current.ready = (async () => {
      await client.connect(transport);
      if (this.connection !== current || this.closed) throw new Error('OpenViking MCP connection closed during startup');
      const tools = await client.listTools({ timeoutMs: this.handshakeMs });
      if (this.connection !== current || this.closed) throw new Error('OpenViking MCP connection closed during startup');
      return tools;
    })();
    current.ready.catch(() => {
      if (this.connection === current) this.connection = null;
    });
    return current;
  }

  // Handshake plus tools/list; rejects on failure, leaving no connection.
  async connect() {
    const config = this.readConfig();
    if (!config) throw new Error('OpenViking configuration is unavailable');
    const current = this.open(config);
    return await current.ready;
  }

  async callTool(name, args) {
    if (this.closed) throw new Error('OpenViking MCP client is closed');
    const config = this.readConfig();
    if (!config) throw new Error('OpenViking configuration is unavailable');
    const current = this.open(config);
    try {
      await current.ready;
      return await current.client.callTool(name, args ?? {}, { timeoutMs: this.callTimeoutMs });
    } catch (error) {
      if (!(error instanceof McpError) && this.connection === current) {
        this.connection = null;
        void current.client.close().catch(() => {});
      }
      throw error;
    }
  }

  async close() {
    this.closed = true;
    const current = this.connection;
    this.connection = null;
    await current?.client.close().catch(() => {});
  }
}