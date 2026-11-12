#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { actionEndpoint, callTool, listTools } from './client.mjs';
import { parseUniqueJson } from '../unique-json.mjs';
import { inlineImageBlocks } from '../inline-images.mjs';

// Stdio MCP adapter for local runtimes (Codex, TraeX, Claude, Pi, DSH, OpenCode, Antigravity).
// It relays tools/list and tools/call to the Core and renders results; nothing
// else.
const MAX_REQUEST_BYTES = 1_000_000;

// Tools a read-only instance withholds. Delivery and decision tools change
// something outside this process, so a scoped agent (for example a DSH child
// agent) must not see them at all: absence is a boundary a prompt cannot argue
// past, while a policy check inside a shared process cannot even tell which
// agent called.
const DEFAULT_WRITE_TOOLS = Object.freeze([
  'send_message', 'notify_owner', 'reply_message', 'react_to_message', 'retract_message',
  'stay_silent', 'create_timer', 'cancel_timer', 'finish_timer_occurrence', 'read_meeting_inbox',
]);

export function createMcpHandler(endpoint, { readOnly = false, writeTools = DEFAULT_WRITE_TOOLS } = {}) {
  const withheld = readOnly ? new Set(writeTools) : null;
  const isWithheld = (name) => Boolean(withheld && withheld.has(String(name ?? '')));
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
      const tools = await listTools(endpoint, { signal });
      // The list is what the model can call, so withholding here is the whole
      // feature; tools/call refuses the same names for a client that ignores it.
      return { jsonrpc: '2.0', id, result: { tools: withheld ? tools.filter(tool => !isWithheld(tool?.name)) : tools } };
    }
    if (message.method === 'tools/call') {
      if (isWithheld(message.params?.name)) {
        return { jsonrpc: '2.0', id, result: {
          content: [{ type: 'text', text: `This agent cannot call ${message.params.name}: the tool is not available to it.` }],
          isError: true,
        } };
      }
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
  // `--name value` pairs, plus bare `--flag` spellings that stand for `--flag 1`
  // so a boolean option cannot swallow the following option as its value.
  const values = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token?.startsWith('--')) continue;
    const next = argv[index + 1];
    const bare = next === undefined || next.startsWith('--');
    values[token.replace(/^--/, '')] = bare ? '1' : (index += 1, next);
  }
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
  }), {
    readOnly: args.readonly === '1' || args['read-only'] === '1' || args.readonly === 'true',
    ...(args['write-tools'] ? { writeTools: String(args['write-tools']).split(',').map(name => name.trim()).filter(Boolean) } : {}),
  });
  await serveMcp({ input: process.stdin, output: process.stdout, handle });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  serve().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}
