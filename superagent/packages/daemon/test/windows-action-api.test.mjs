import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { localSocketPath } from '../src/platform/index.mjs';
import { startActionApi, stopActionApi } from '../src/actions/api.mjs';
import { requestAction } from '../src/actions/client.mjs';

test('action API uses native local transport and passes runtime authority', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-actions-'));
  const socketPath = localSocketPath(directory);
  const service = {
    tools: () => [{ name: 'test' }],
    call: async (tool, input, context) => {
      if (context.authority !== 'test-authority') throw new Error('unauthorized');
      return { text: `${tool}:${input.value}` };
    },
  };
  let server;
  try {
    server = await startActionApi({ socketPath, service, logger: {} });
    const result = await requestAction({ socketPath, authority: 'test-authority', client: 'mcp' }, 'POST', '/v1/call', { tool: 'test', input: { value: 'hello' } });
    assert.equal(result.result.text, 'test:hello');
    const refused = await requestAction({ socketPath, authority: 'wrong', client: 'mcp' }, 'POST', '/v1/call', { tool: 'test' });
    assert.equal(refused.ok, false);
  } finally {
    await stopActionApi(server);
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('a pre-bound Windows pipe is refused rather than reusing an unverified server', { skip: process.platform !== 'win32' }, async () => {
  const { createServer } = await import('node:net');
  const socketPath = localSocketPath(path.join(os.tmpdir(), 'superagent-squatting-test'), 'occupied');
  const occupied = createServer();
  await new Promise((resolve, reject) => occupied.once('error', reject).listen(socketPath, resolve));
  try {
    await assert.rejects(startActionApi({ socketPath, service: {}, logger: {} }), { code: 'EADDRINUSE' });
  } finally { await new Promise(resolve => occupied.close(resolve)); }
});
