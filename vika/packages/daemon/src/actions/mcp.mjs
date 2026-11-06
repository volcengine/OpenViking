#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { actionEndpoint, callTool, listTools } from './client.mjs';
import { parseUniqueJson } from '../unique-json.mjs';
import { inlineImageBlocks } from '../inline-images.mjs';

// Stdio MCP adapter for local runtimes (Codex, TraeX, Claude, Pi, OpenCode).
// It relays tools/list and tools/call to the Core and renders results; nothing
// else.
const MAX_REQUEST_BYTES = 1_000_000;

export function createMcpHandler(endpoint) {
  return async function handle(message, { signal } = {}) {
    signal?.throwIfAborted();
    const id = message?.id;
    if (message?.jsonrpc !== '2.0' || typeof message.method !== 'string') {
      return id === undefined ? null : { jsonrpc: '2.0', id, error: { code: -32600, message: 'Invalid Request' } };
    }
    if (id === undefined) return null; // notifications
    if (message.method === 'initialize') {
      return { jsonrpc: '2.0', id, result: {
        protocolVersion: message.params?.protocolVersion ?? '2025-06-18',
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: 'personal-agent', version: '2.0.0' },
      } };
    }
    if (message.method === 'ping') return { jsonrpc: '2.0', id, result: {} };
    if (message.method === 'tools/list') {
      return { jsonrpc: '2.0', id, result: { tools: await listTools(endpoint, { signal }) } };
    }
    if (message.method === 'tools/call') {
      try {
        const result = await callTool(endpoint, message.params?.name, message.params?.arguments ?? {}, { signal });
        signal?.throwIfAborted();
        const images = await inlineImageBlocks(result.media);
        signal?.throwIfAborted();
        return { jsonrpc: '2.0', id, result: {
          content: [{ type: 'text', text: result.text }, ...images],
          structuredContent: result.data,
        } };
      } catch (error) {
        return { jsonrpc: '2.0', id, result: {
          content: [{ type: 'text', text: error instanceof Error ? error.message : String(error) }], isError: true,
        } };
      }
    }
    return { jsonrpc: '2.0', id, error: { code: -32601, message: 'Method not found' } };
  };
}

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) values[argv[index].replace(/^--/, '')] = argv[index + 1];
  return values;
}

// A request may await Core while subsequent requests (including cancellation)
// arrive. Keep transport ingestion independent of execution and correlate by id.
export async function serveMcp({ input, output, handle }) {
  const requests = new Map();
  const pending = new Set();
  const write = (message) => output.write(`${JSON.stringify(message)}\n`);
  const dispatch = (line) => {
    if (!line.trim()) return;
    let message;
    try { message = parseUniqueJson(line); } catch {
      write({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } });
      return;
    }
    if (message?.jsonrpc === '2.0' && message.method === 'notifications/cancelled' && message.id === undefined) {
      const request = requests.get(message.params?.requestId);
      if (request && request.method !== 'initialize') request.controller.abort();
      return;
    }
    if (message?.id === undefined) return; // notifications need no response
    const id = message.id;
    if (requests.has(id)) {
      write({ jsonrpc: '2.0', id, error: { code: -32600, message: 'Request id is already in flight' } });
      return;
    }
    const controller = new AbortController();
    requests.set(id, { controller, method: message.method });
    const task = Promise.resolve().then(() => {
      // A cancellation received in the same batch can prevent dispatch entirely.
      controller.signal.throwIfAborted();
      return handle(message, { signal: controller.signal });
    }).then((response) => {
      if (response && !controller.signal.aborted) write(response);
    }).catch(() => {
      if (!controller.signal.aborted) write({ jsonrpc: '2.0', id, error: { code: -32603, message: 'Internal error' } });
    }).finally(() => {
      requests.delete(id);
      pending.delete(task);
    });
    pending.add(task);
  };

  // Node readline also splits U+2028/U+2029 on some supported Node versions.
  // MCP uses LF framing; those Unicode characters are legal JSON string data.
  input.setEncoding('utf8');
  let buffer = '';
  try {
    for await (const chunk of input) {
      buffer += chunk;
      let index;
      while ((index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index);
        buffer = buffer.slice(index + 1);
        if (Buffer.byteLength(line) > MAX_REQUEST_BYTES) throw new Error('MCP request too large');
        dispatch(line);
      }
      if (Buffer.byteLength(buffer) > MAX_REQUEST_BYTES) throw new Error('MCP request too large');
    }
    if (buffer.trim()) dispatch(buffer);
    // EOF may immediately follow a batch of calls; let accepted calls finish.
    await Promise.allSettled(pending);
  } finally {
    // Abort the local HTTP waits. This cannot undo a delivery Core has already
    // submitted to a connector; outbox remains authoritative for its outcome.
    for (const { controller } of requests.values()) controller.abort();
  }
}

async function serve() {
  const args = parseArgs(process.argv.slice(2));
  const handle = createMcpHandler(actionEndpoint('mcp', {
    PERSONAL_AGENT_SOCKET: args.socket ?? process.env.PERSONAL_AGENT_SOCKET,
    PERSONAL_AGENT_AUTHORITY: args.authority ?? process.env.PERSONAL_AGENT_AUTHORITY,
  }));
  await serveMcp({ input: process.stdin, output: process.stdout, handle });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  serve().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}
