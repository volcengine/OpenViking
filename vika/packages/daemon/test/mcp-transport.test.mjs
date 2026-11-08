// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { McpClient, StdioTransport } from '@earendil-works/pi-mcp';
import { createMcpHandler, serveMcp } from '../src/actions/mcp.mjs';

const mcpPath = fileURLToPath(new URL('../src/actions/mcp.mjs', import.meta.url));
const deferred = () => Promise.withResolvers();

async function fixture(t, run) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mcp-transport-'));
  const socketPath = path.join(root, 'actions.sock');
  const server = http.createServer(async (request, response) => {
    let body = '';
    for await (const chunk of request) body += chunk;
    await run(JSON.parse(body), response);
  });
  const client = new McpClient({ clientInfo: { name: 'transport-test', version: '1' }, requestTimeoutMs: 3_000 });
  t.after(async () => {
    await client.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    fs.rmSync(root, { recursive: true, force: true });
  });
  await new Promise((resolve) => server.listen(socketPath, resolve));
  await client.connect(new StdioTransport({ command: process.execPath,
    args: [mcpPath, '--socket', socketPath, '--authority', 'fixture'] }));
  return client;
}

test('MCP framing preserves split UTF-8 and Unicode separators, accepts CRLF, and recovers after malformed JSON', async () => {
  const text = '中文\u2028line\u2029paragraph\nquoted "text"';
  const valid = JSON.stringify({ jsonrpc: '2.0', id: 0, method: 'echo', params: { text } });
  const wire = Buffer.from(`broken\n${valid}\r\n${valid.replace('"id":0', '"id":1')}\n`);
  const responses = [];
  await serveMcp({
    input: Readable.from([...wire].map((byte) => Buffer.from([byte]))),
    output: { write: (line) => responses.push(JSON.parse(line)) },
    handle: async (message) => ({ jsonrpc: '2.0', id: message.id, result: message.params }),
  });
  assert.equal(responses[0].error.code, -32700);
  assert.deepEqual(responses.slice(1).map((message) => message.result.text), [text, text]);
});

test('real MCP client sends Unicode separators through the actual adapter without timing out', { timeout: 10_000 }, async (t) => {
  const client = await fixture(t, async ({ input }, response) => {
    response.end(JSON.stringify({ ok: true, result: { text: input.text, data: input } }));
  });
  const text = 'before\u2028中间\u2029after';
  const result = await client.callTool('echo', { text });
  assert.equal(result.content[0].text, text);
  assert.equal(result.structuredContent.text, text);
});

test('a reply completes while a concurrent media read is still blocked in Core', { timeout: 10_000 }, async (t) => {
  const mediaStarted = deferred();
  const releaseMedia = deferred();
  let mediaFinished = false;
  const client = await fixture(t, async ({ tool }, response) => {
    if (tool === 'get_message_media') {
      mediaStarted.resolve();
      await releaseMedia.promise;
      mediaFinished = true;
    }
    response.end(JSON.stringify({ ok: true, result: { text: tool, data: {} } }));
  });
  const media = client.callTool('get_message_media', {});
  // Observe rejection immediately even if the fast-call assertion fails.
  media.catch(() => {});
  try {
    await mediaStarted.promise;
    const reply = await client.callTool('reply_message', {});
    assert.equal(reply.content[0].text, 'reply_message');
    assert.equal(mediaFinished, false, 'reply must not wait for the slow call');
  } finally { releaseMedia.resolve(); }
  assert.equal((await media).content[0].text, 'get_message_media');
});

test('cancellation is consumed while another request is pending and aborts its HTTP wait', { timeout: 10_000 }, async (t) => {
  const started = deferred();
  const disconnected = deferred();
  const client = await fixture(t, async (_body, response) => {
    response.once('close', () => disconnected.resolve());
    started.resolve();
    // Core deliberately never responds. The adapter must cancel its local wait.
  });
  const controller = new AbortController();
  const call = client.callTool('slow', {}, { signal: controller.signal });
  const rejected = assert.rejects(call, /abort/i);
  await started.promise;
  controller.abort();
  await rejected;
  await disconnected.promise;
  assert.deepEqual(await client.request('ping'), {});
});

test('same-batch cancellation prevents undispatched work and does not affect other request ids', async () => {
  const messages = [
    { jsonrpc: '2.0', id: 'cancel-me', method: 'tools/call' },
    { jsonrpc: '2.0', method: 'notifications/cancelled', params: { requestId: 'cancel-me' } },
    { jsonrpc: '2.0', id: 'keep-me', method: 'ping' },
  ];
  const called = [];
  const responses = [];
  await serveMcp({
    input: Readable.from([messages.map((message) => JSON.stringify(message)).join('\n') + '\n']),
    output: { write: (line) => responses.push(JSON.parse(line)) },
    handle: async (message) => {
      called.push(message.id);
      return { jsonrpc: '2.0', id: message.id, result: {} };
    },
  });
  assert.deepEqual(called, ['keep-me']);
  assert.deepEqual(responses.map((message) => message.id), ['keep-me']);
});

test('a failed tools/list returns a correlated error and does not kill other requests', async () => {
  const responses = [];
  await serveMcp({
    input: Readable.from([
      JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }) + '\n'
      + JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'ping' }) + '\n',
    ]),
    output: { write: (line) => responses.push(JSON.parse(line)) },
    handle: createMcpHandler({ socketPath: '/nonexistent/lark-mcp-test.sock' }),
  });
  assert.equal(responses.find(({ id }) => id === 1).error.code, -32603);
  assert.deepEqual(responses.find(({ id }) => id === 2).result, {});
});
