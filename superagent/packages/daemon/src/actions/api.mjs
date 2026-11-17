// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { isNamedPipe } from '../platform/index.mjs';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { redactSensitive, safeError } from '../logger.mjs';
import { parseUniqueJson } from '../unique-json.mjs';

const MAX_BODY_BYTES = 300_000;
export const AUTHORITY_HEADER = 'x-agent-authority';
export const CLIENT_HEADER = 'x-agent-client';

function send(response, status, body) {
  const payload = JSON.stringify(body);
  response.writeHead(status, {
    'cache-control': 'no-store',
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(payload),
  });
  response.end(payload);
}

async function readJson(request) {
  let body = '';
  for await (const chunk of request) {
    body += chunk;
    if (Buffer.byteLength(body, 'utf8') > MAX_BODY_BYTES) throw new Error('request body too large');
  }
  const parsed = parseUniqueJson(body);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('request body must be an object');
  return parsed;
}

// The agent tool endpoint on a mode-0600 Unix socket. The MCP adapter only
// relays to it; the ActionService decides and executes.
//   GET  /v1/tools -> { tools }
//   POST /v1/call  { tool, input } -> { ok, result: { text, data, media? } } | { ok: false, error }
export async function startActionApi({ socketPath, service, logger }) {
  if (!isNamedPipe(socketPath)) {
    fs.mkdirSync(path.dirname(socketPath), { recursive: true, mode: 0o700 });
    fs.rmSync(socketPath, { force: true });
  }
  const server = http.createServer(async (request, response) => {
    if (request.method === 'GET' && request.url === '/v1/tools') {
      send(response, 200, { tools: service.tools({ authority: request.headers[AUTHORITY_HEADER] }) });
      return;
    }
    if (request.method !== 'POST' || request.url !== '/v1/call') {
      send(response, 404, { ok: false, error: 'not found' });
      return;
    }
    let body;
    try { body = await readJson(request); } catch (error) {
      send(response, 400, { ok: false, error: safeError(error) });
      return;
    }
    try {
      const result = await service.call(body.tool, body.input ?? {}, {
        authority: request.headers[AUTHORITY_HEADER],
        via: request.headers[CLIENT_HEADER] === 'mcp' ? 'mcp' : 'unknown',
      });
      send(response, 200, { ok: true, result });
    } catch (error) {
      // ActionService.call already logged the refusal with its code.
      const message = redactSensitive(error instanceof Error ? error.message : String(error)).slice(0, 2_000);
      send(response, 200, { ok: false, error: message });
    }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(socketPath, () => {
      server.off('error', reject);
      resolve();
    });
  });
  if (!isNamedPipe(socketPath)) fs.chmodSync(socketPath, 0o600);
  return server;
}

export async function stopActionApi(server) {
  if (!server) return;
  const socketPath = server.address();
  await new Promise((resolve) => server.close(resolve));
  if (typeof socketPath === 'string' && !isNamedPipe(socketPath)) fs.rmSync(socketPath, { force: true });
}

// How an MCP-capable runtime launches the adapter for this daemon's tools.
//
// No `--readonly` here: every runtime gets the full surface, and so does a DSH
// subagent, which shares its parent's mount. The adapter's read-only mode exists
// (`actions/mcp.mjs`) but is unreachable until a runtime can mount a second,
// narrower server for a subagent -- DSH cannot, so adding the flag for the whole
// process would only take `send_message` away from the parent as well.
export function agentToolServer(config, authority) {
  return {
    command: process.execPath,
    args: [config.actionMcpPath, '--socket', config.actionSocketPath, '--authority', authority],
  };
}

export function agentToolEnv(config, authority) {
  return { socketPath: config.actionSocketPath, authority };
}
