// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';
import test from 'node:test';
import { parseUniqueJson } from '../src/unique-json.mjs';
import { serveMcp } from '../src/actions/mcp.mjs';
import { startActionApi, stopActionApi } from '../src/actions/api.mjs';

const duplicate = '{"card_images":{"cover":"a.png","co\\u0076er":"b.png"}}';

test('rejects repeated aliases including escaped spellings, without confusing values or nested objects', () => {
  assert.throws(() => parseUniqueJson(duplicate), /duplicate JSON key: cover/);
  const valid = { a: { cover: 'x' }, b: { cover: 'y' }, c: ['a', 'b'], d: 'braces {} and \\"quoted\\" values' };
  assert.deepEqual(parseUniqueJson(JSON.stringify(valid)), valid);
});

test('MCP rejects duplicate aliases before forwarding a tool call', async () => {
  let calls = 0;
  const responses = [];
  await serveMcp({ input: Readable.from([`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"send_message","arguments":${duplicate}}}\n`]),
    output: { write(line) { responses.push(JSON.parse(line)); } }, handle: async () => { calls++; } });
  assert.equal(calls, 0);
  assert.equal(responses[0].error.code, -32700);
});

test('Core HTTP API rejects duplicate aliases before authority or delivery handling', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'unique-json-'));
  const socketPath = path.join(root, 'api.sock');
  let calls = 0;
  const server = await startActionApi({ socketPath, service: { call() { calls++; } }, logger: {} });
  t.after(async () => { await stopActionApi(server); fs.rmSync(root, { recursive: true, force: true }); });
  const result = await new Promise((resolve, reject) => {
    const req = http.request({ socketPath, path: '/v1/call', method: 'POST' }, (res) => {
      let body = '';
      res.on('data', chunk => { body += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, body }));
    });
    req.on('error', reject);
    req.end(`{"tool":"send_message","input":${duplicate}}`);
  });
  assert.equal(result.status, 400);
  assert.match(result.body, /duplicate JSON key/);
  assert.equal(calls, 0);
});
