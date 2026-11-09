// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { probeAcp, probeAgyAcp, probeAppServer } from '../src/runtime-probe.mjs';

function fixture(t, script) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-app-server-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const bin = path.join(dir, 'fake-cli');
  fs.writeFileSync(bin, `#!${process.execPath}\n${script}`, { mode: 0o755 });
  const children = [];
  const closed = [];
  const spawnImpl = (...args) => {
    const child = spawn(...args);
    children.push(child);
    closed.push(once(child, 'close'));
    return child;
  };
  return { bin, dir, children, closed, options: { env: { PATH: dir, HOME: dir }, spawnImpl, timeoutMs: 1000 } };
}

test('probe initializes stdio and exits without creating a thread or running a turn', async (t) => {
  const f = fixture(t, `
    const fs = require('node:fs');
    const readline = require('node:readline');
    if (JSON.stringify(process.argv.slice(2)) !== JSON.stringify(['app-server', '--listen', 'stdio://'])) process.exit(2);
    readline.createInterface({ input: process.stdin }).on('line', (line) => {
      const request = JSON.parse(line);
      fs.appendFileSync(process.env.HOME + '/requests', request.method + '\\n');
      if (request.method === 'initialize') {
        process.stdout.write('{"id":1,"res');
        setTimeout(() => process.stdout.write('ult":{}}\\n'), 5);
      } else if (request.method !== 'initialized') process.exit(3);
    });
  `);
  assert.equal((await probeAppServer(f.bin, f.options)).compatible, true);
  await Promise.all(f.closed);
  const requests = fs.readFileSync(path.join(f.dir, 'requests'), 'utf8').trim().split('\n');
  assert.equal(requests[0], 'initialize');
  assert.ok(requests.every((method) => ['initialize', 'initialized'].includes(method)));
  assert.equal(f.children[0].signalCode, 'SIGKILL');
});

test('probe rejects a CLI that exits successfully without speaking app-server', async (t) => {
  const f = fixture(t, 'process.exit(0)');
  const result = await probeAppServer(f.bin, f.options);
  assert.equal(result.compatible, false);
  assert.match(result.reason, /exited before initialize/);
});

test('probe preserves useful stderr but redacts secrets split across chunks', async (t) => {
  const f = fixture(t, `
    process.stderr.write('unrecognized app-server; Authorization: Bearer sup');
    setTimeout(() => { process.stderr.write('er-secret-value\\n'); process.exit(1); }, 5);
  `);
  const result = await probeAppServer(f.bin, f.options);
  assert.equal(result.compatible, false);
  assert.match(result.reason, /unrecognized app-server/);
  assert.doesNotMatch(result.reason, /super-secret-value/);
});

test('probe times out and terminates an unresponsive CLI', async (t) => {
  const f = fixture(t, 'setInterval(() => {}, 1000)');
  const result = await probeAppServer(f.bin, { ...f.options, timeoutMs: 100 });
  assert.equal(result.compatible, false);
  assert.match(result.reason, /timed out/);
  await Promise.all(f.closed);
  assert.equal(f.children[0].signalCode, 'SIGKILL');
});

test('probe rejects a JSON-RPC initialize error', async (t) => {
  const f = fixture(t, `process.stdin.once('data', () => {
    process.stdout.write('{"id":1,"error":{"code":-32601,"message":"method not found"}}\\n');
  });`);
  assert.equal((await probeAppServer(f.bin, f.options)).compatible, false);
  await Promise.all(f.closed);
});

test('probe bounds output from a misbehaving CLI and kills it', async (t) => {
  const f = fixture(t, `process.stdout.write('x'.repeat(100000)); setInterval(() => {}, 1000);`);
  const result = await probeAppServer(f.bin, f.options);
  assert.equal(result.compatible, false);
  assert.match(result.reason, /exceeded 64 KiB/);
  await Promise.all(f.closed);
});

test('probe handles a spawn error without an unhandled rejection', async () => {
  const result = await probeAppServer('/not-used', { spawnImpl: () => { throw new Error('missing executable'); } });
  assert.equal(result.compatible, false);
  assert.match(result.reason, /missing executable/);
});

test('ACP probe sends only initialize to `acp` and accepts an OpenCode answer', async (t) => {
  const f = fixture(t, `
    const fs = require('node:fs');
    const readline = require('node:readline');
    if (JSON.stringify(process.argv.slice(2)) !== JSON.stringify(['acp'])) process.exit(2);
    readline.createInterface({ input: process.stdin }).on('line', (line) => {
      const request = JSON.parse(line);
      fs.appendFileSync(process.env.HOME + '/requests', line + '\\n');
      if (request.method !== 'initialize') process.exit(3);
      process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: { protocolVersion: 1, agentInfo: { name: 'OpenCode' } } }) + '\\n');
    });
  `);
  assert.equal((await probeAcp(f.bin, f.options)).compatible, true);
  await Promise.all(f.closed);
  const requests = fs.readFileSync(path.join(f.dir, 'requests'), 'utf8').trim().split('\n').map((line) => JSON.parse(line));
  assert.deepEqual(requests.map((request) => request.method), ['initialize']);
  assert.equal(requests[0].jsonrpc, '2.0');
  assert.equal(requests[0].params.protocolVersion, 1);
  assert.equal(f.children[0].signalCode, 'SIGKILL');
});

function agyServer(result) {
  return `
    const readline = require('node:readline');
    if (JSON.stringify(process.argv.slice(2)) !== JSON.stringify(['acp'])) process.exit(2);
    readline.createInterface({ input: process.stdin }).on('line', (line) => {
      const request = JSON.parse(line);
      process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: ${JSON.stringify(result)} }) + '\\n');
    });
  `;
}

test('Antigravity probe needs the agent name and the append capability', async (t) => {
  const good = fixture(t, agyServer({
    agentInfo: { name: 'Antigravity ACP Server', version: '1.5.3' },
    agentCapabilities: { _meta: { antigravity: { systemPrompt: { append: true } } } },
  }));
  assert.equal((await probeAgyAcp(good.bin, good.options)).compatible, true);
  await Promise.all(good.closed);

  const other = fixture(t, agyServer({ agentInfo: { name: 'OpenCode' } }));
  const refused = await probeAgyAcp(other.bin, other.options);
  assert.equal(refused.compatible, false);
  assert.match(refused.reason, /unrecognised ACP agent/);
  await Promise.all(other.closed);

  const old = fixture(t, agyServer({ agentInfo: { name: 'Antigravity ACP Server' }, agentCapabilities: {} }));
  const stale = await probeAgyAcp(old.bin, old.options);
  assert.equal(stale.compatible, false);
  assert.match(stale.reason, /cannot append a system prompt/);
  await Promise.all(old.closed);
});

test('ACP probe rejects an initialize error', async (t) => {
  const f = fixture(t, `process.stdin.once('data', () => {
    process.stdout.write('{"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"method not found"}}\\n');
  });`);
  const result = await probeAcp(f.bin, f.options);
  assert.equal(result.compatible, false);
  assert.match(result.reason, /ACP rejected initialize/);
  await Promise.all(f.closed);
});
