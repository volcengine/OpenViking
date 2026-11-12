import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import { createAuthProof, tokenMatches } from '../dist/auth.js';
import { BridgeServer } from '../dist/server.js';

const TOKEN = 'test-token-with-at-least-thirty-two-bytes';

class FakeSocket extends EventEmitter {
  constructor() {
    super();
    this.sent = [];
    this.closeCode = null;
  }

  send(raw) {
    this.sent.push(JSON.parse(raw));
  }

  close(code) {
    this.closeCode = code;
    this.emit('close');
  }
}

test('proofs are bound to both the token and challenge', () => {
  const challenge = 'a'.repeat(64);
  assert.notEqual(createAuthProof(TOKEN, challenge), createAuthProof(`${TOKEN}x`, challenge));
  assert.notEqual(createAuthProof(TOKEN, challenge), createAuthProof(TOKEN, 'b'.repeat(64)));
  assert.equal(tokenMatches(TOKEN, TOKEN), true);
  assert.equal(tokenMatches(`${TOKEN}x`, TOKEN), false);
  assert.equal(tokenMatches(null, TOKEN), false);
});

test('the bridge requires proof before it accepts the bearer', () => {
  const server = new BridgeServer(0, '/unused', TOKEN);
  const rejected = new FakeSocket();
  server.authenticateClient(rejected);
  rejected.emit('message', Buffer.from(JSON.stringify({ type: 'auth', token: TOKEN })));
  assert.equal(rejected.closeCode, 4003);

  const client = new FakeSocket();
  server.authenticateClient(client);
  const challenge = 'c'.repeat(64);
  client.emit('message', Buffer.from(JSON.stringify({ type: 'auth_challenge', challenge })));
  assert.deepEqual(client.sent.shift(), {
    type: 'auth_proof',
    proof: createAuthProof(TOKEN, challenge),
  });

  client.emit('message', Buffer.from(JSON.stringify({ type: 'auth', token: TOKEN })));
  assert.deepEqual(client.sent.shift(), { type: 'auth_ok' });
  assert.equal(client.closeCode, null);
});

test('the bridge entrypoint fails closed without a token', () => {
  const env = { ...process.env };
  delete env.BRIDGE_TOKEN;
  const result = spawnSync(process.execPath, ['dist/index.js'], {
    cwd: new URL('..', import.meta.url),
    env,
    encoding: 'utf8',
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /BRIDGE_TOKEN is required/);
});
