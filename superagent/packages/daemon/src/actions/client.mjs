// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import http from 'node:http';
import { AUTHORITY_HEADER, CLIENT_HEADER } from './api.mjs';

// Used by the MCP adapter. The socket path and runtime authority
// come from the agent process environment the daemon set up.
export function actionEndpoint(client, environment = process.env) {
  const socketPath = environment.PERSONAL_AGENT_SOCKET;
  if (!socketPath) throw new Error('PERSONAL_AGENT_SOCKET is not set; the agent tools are only available inside a daemon runtime');
  return { socketPath, authority: environment.PERSONAL_AGENT_AUTHORITY ?? '', client };
}

export function requestAction({ socketPath, authority, client }, method, route, body = null, { signal } = {}) {
  const payload = body === null ? '' : JSON.stringify(body);
  return new Promise((resolve, reject) => {
    const request = http.request({
      socketPath, path: route, method, signal,
      headers: {
        [AUTHORITY_HEADER]: authority,
        [CLIENT_HEADER]: client,
        ...(body === null ? {} : { 'content-type': 'application/json', 'content-length': Buffer.byteLength(payload) }),
      },
    }, (response) => {
      let output = '';
      response.setEncoding('utf8');
      response.once('error', reject);
      response.on('data', (chunk) => { output += chunk; });
      response.on('end', () => {
        try { resolve(JSON.parse(output)); } catch {
          reject(new Error(`agent tool endpoint returned HTTP ${response.statusCode}`));
        }
      });
    });
    request.once('error', reject);
    request.end(payload);
  });
}

export async function listTools(endpoint, options) {
  return (await requestAction(endpoint, 'GET', '/v1/tools', null, options)).tools;
}

export async function callTool(endpoint, tool, input, options) {
  const response = await requestAction(endpoint, 'POST', '/v1/call', { tool, input }, options);
  if (!response.ok) throw new Error(response.error || 'agent tool failed');
  return response.result;
}
