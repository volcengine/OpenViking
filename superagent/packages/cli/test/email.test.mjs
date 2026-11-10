// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { askEmail, emailInstanceId, providerFor, writeEmailAccount } from '../src/email.mjs';
import { Prompter } from '../src/prompt.mjs';

function terminal(answers) {
  const input = new PassThrough();
  input.end(answers.map((line) => `${line}\n`).join(''));
  const output = new PassThrough();
  let written = '';
  output.on('data', (chunk) => { written += chunk; });
  return { prompter: new Prompter({ input, output }), written: () => written };
}

test('a known provider needs only the address and its client password, spaces dropped', async () => {
  const tried = [];
  const { prompter, written } = terminal(['Alice.Example@Gmail.com', 'abcd efgh ijkl mnop']);
  const account = await askEmail({ prompter, verify: async (given) => { tried.push(given); return { idle: true }; } });
  assert.deepEqual(account, {
    address: 'alice.example@gmail.com', password: 'abcdefghijklmnop',
    imap: { host: 'imap.gmail.com', port: 993 }, smtp: { host: 'smtp.gmail.com', port: 465 },
  });
  assert.deepEqual(tried, [{ ...account, user: 'alice.example@gmail.com' }]);
  assert.match(written(), /Gmail：密码处填应用专用密码/);
  assert.doesNotMatch(written(), /abcd/, 'the password is never echoed');
  assert.deepEqual([providerFor('a@qq.com').imap, providerFor('a@163.com').smtp, providerFor('a@126.com').imap],
    ['imap.qq.com', 'smtp.163.com', 'imap.126.com']);
});

test('a failed login says why and asks again; an unknown provider asks for its servers', async () => {
  let attempts = 0;
  const verify = async ({ password }) => {
    attempts += 1;
    if (password !== 'right') throw new Error('IMAP LOGIN failed: NO LOGIN Login error or password error');
    return { idle: false };
  };
  const { prompter, written } = terminal([
    'me@corp.example', '', '', 'mail.corp.example', '587', 'wrong pass', 'y',
    '', '', '', '', '', 'right',
  ]);
  const account = await askEmail({ prompter, verify });
  assert.equal(attempts, 2);
  // Servers default to imap./smtp. of the domain, then to what was typed last time.
  assert.deepEqual(account, {
    address: 'me@corp.example', password: 'right',
    imap: { host: 'imap.corp.example', port: 993 }, smtp: { host: 'mail.corp.example', port: 587 },
  });
  assert.match(written(), /登录失败：IMAP LOGIN failed[\s\S]*多半是密码不对/);
  assert.match(written(), /每分钟查一次/);

  const outlook = terminal(['me@outlook.com']);
  await assert.rejects(askEmail({ prompter: outlook.prompter, verify }));
  assert.match(outlook.written(), /只能用 OAuth 登录/);

  const givenUp = terminal(['me@qq.com', 'nope', 'n']);
  await assert.rejects(askEmail({ prompter: givenUp.prompter, verify }), { name: 'PromptAborted' });
});

test('a new password keeps the saved servers; an address already watched is refused; names read as the provider', async () => {
  const saved = { address: 'me@corp.example', password: 'old', imap: { host: 'mail.corp.example', port: 993 }, smtp: { host: 'mail.corp.example', port: 587 } };
  const update = terminal(['new-password']);
  const account = await askEmail({ prompter: update.prompter, fixed: saved, verify: async () => ({ idle: true }) });
  assert.deepEqual(account, { ...saved, password: 'new-password' });
  assert.doesNotMatch(update.written(), /邮箱地址|服务器/);

  const twice = terminal(['10001@qq.com', 'alice@163.com', 'code']);
  assert.equal((await askEmail({ prompter: twice.prompter, taken: ['10001@qq.com'], verify: async () => ({ idle: true }) })).address,
    'alice@163.com');
  assert.match(twice.written(), /这个邮箱已经接入了/);

  assert.deepEqual([emailInstanceId('alice.example@gmail.com'), emailInstanceId('10001@qq.com'), emailInstanceId('alice@163.com')],
    ['email-gmail', 'email-qq', 'email-163']);
  assert.equal(emailInstanceId('a.b+c@gmail.com', ['email-gmail']), 'email-gmail-a-b-c');
  assert.equal(emailInstanceId('x@gmail.com', ['email-gmail', 'email-gmail-x']), 'email-gmail-2');
});

test('the account file is private', (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-email-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const file = path.join(directory, 'email', 'primary', 'email-qq.json');
  writeEmailAccount(file, { address: 'a@qq.com', password: 'p' });
  assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  assert.equal(fs.statSync(path.dirname(file)).mode & 0o777, 0o700);
  assert.deepEqual(fs.readdirSync(path.dirname(file)), ['email-qq.json']);
});
