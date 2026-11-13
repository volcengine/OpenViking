// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { it } from 'node:test';
import { CodexAuthMonitor, codexAccountSummary, codexAuthIdentity } from '../src/codex-auth.mjs';

function credentials(user, account, token = 'access-secret') {
  const payload = { sub: user, email: `${user}@example.com`, 'https://api.openai.com/auth': { chatgpt_account_id: account } };
  return { auth_mode: 'chatgpt', tokens: { account_id: account, access_token: token, refresh_token: token,
    id_token: `header.${Buffer.from(JSON.stringify(payload)).toString('base64url')}.signature` } };
}

it('tracks user/workspace identity while ignoring token rotation and timestamps', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-auth-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'auth.json');
  const identity = (value) => { fs.writeFileSync(file, JSON.stringify(value)); return codexAuthIdentity(file); };
  const original = identity(credentials('alice', 'workspace-a'));
  assert.equal(original, identity({ ...credentials('alice', 'workspace-a', 'rotated-secret'), last_refresh: 'later' }));
  assert.notEqual(original, identity(credentials('bob', 'workspace-a')));
  assert.notEqual(original, identity(credentials('alice', 'workspace-b')));
  const signedInAgain = credentials('alice', 'workspace-a');
  const payload = JSON.parse(Buffer.from(signedInAgain.tokens.id_token.split('.')[1], 'base64url'));
  payload.auth_time = 1234;
  signedInAgain.tokens.id_token = `header.${Buffer.from(JSON.stringify(payload)).toString('base64url')}.signature`;
  assert.notEqual(original, identity(signedInAgain), 'a new login session can replace revoked same-account credentials');
  assert.doesNotMatch(original, /alice|workspace|secret/);
  const api = identity({ OPENAI_API_KEY: 'api-secret-a' });
  assert.notEqual(api, identity({ OPENAI_API_KEY: 'api-secret-b' }));
  assert.doesNotMatch(api, /api-secret/);
});

it('ignores incomplete, oversized and malformed auth writes and recognizes logout', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-auth-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'auth.json');
  assert.equal(codexAuthIdentity(file), 'signed-out');
  for (const text of ['{', 'null', '[]', '{"auth_mode":"chatgpt"}', '{"tokens":{"id_token":"invalid"}}', 'x'.repeat(1024 * 1024 + 1)]) {
    fs.writeFileSync(file, text);
    assert.equal(codexAuthIdentity(file), null);
  }
  fs.writeFileSync(file, '{"tokens":null,"OPENAI_API_KEY":null}');
  assert.equal(codexAuthIdentity(file), 'signed-out');
});

it('debounces atomic replacement/logout-login and cancels transient observations', () => {
  let identity = 'account-a';
  let changes = 0;
  const monitor = new CodexAuthMonitor('/unused', () => { changes += 1; }, { readIdentity: () => identity, intervalMs: 60_000 });
  monitor.start();
  try {
    identity = 'signed-out'; monitor.poll();
    identity = 'account-b'; monitor.poll();
    identity = null; monitor.poll();
    identity = 'account-b'; monitor.poll();
    assert.equal(changes, 0);
    monitor.poll();
    assert.equal(changes, 1);
    monitor.poll();
    assert.equal(changes, 1);
    identity = 'account-c'; monitor.poll();
    identity = 'account-b'; monitor.poll();
    assert.equal(changes, 1);
  } finally { monitor.stop(); }
  assert.equal(monitor.timer, null);
});

it('observes real file replacements without being tied to an old inode', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-auth-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'auth.json');
  fs.writeFileSync(file, JSON.stringify(credentials('alice', 'workspace-a')));
  let changes = 0;
  const monitor = new CodexAuthMonitor(file, () => { changes += 1; }, { intervalMs: 60_000 });
  monitor.start();
  t.after(() => monitor.stop());
  const replacement = path.join(dir, 'auth.tmp');
  fs.writeFileSync(replacement, JSON.stringify(credentials('bob', 'workspace-b')));
  fs.renameSync(replacement, file);
  monitor.poll(); monitor.poll();
  assert.equal(changes, 1);
  fs.writeFileSync(file, JSON.stringify(credentials('bob', 'workspace-b', 'new-token')));
  monitor.poll(); monitor.poll();
  assert.equal(changes, 1);
  fs.unlinkSync(file);
  monitor.poll(); monitor.poll();
  assert.equal(changes, 2);
});

it('publishes only masked account details and handles non-email auth modes', () => {
  assert.deepEqual(codexAccountSummary({ account: { type: 'chatgpt', email: 'alice@gmail.com', planType: 'pro', id: 'private-id' } }),
    { type: 'chatgpt', email: 'a***@gmail.com', planType: 'pro' });
  assert.deepEqual(codexAccountSummary({ account: null }), { type: 'signed-out', email: null, planType: null });
  assert.deepEqual(codexAccountSummary({ account: { type: 'apiKey' } }), { type: 'apiKey', email: null, planType: null });
  assert.equal(codexAccountSummary({ account: { email: 'a@x\nforged' } }).email, null);
});
