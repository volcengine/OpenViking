// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { OWNER_DIRECTORY, directoryPerson, personDirectory, personId, topicFile } from '../src/ov-person.mjs';
import { registerNameResolver } from '../src/roster.mjs';
import { chatEvent, mailEvent } from './event-fixture.mjs';

test('a person is their channel identity: namesakes differ, a rename does not, a mail address ignores case', (t) => {
  const names = { ou_a: '张三', ou_b: '张三' };
  registerNameResolver((chatId, id) => names[id] ?? null);
  t.after(() => registerNameResolver(null));
  const a = chatEvent({ senderId: 'ou_a' });
  const b = chatEvent({ senderId: 'ou_b' });
  assert.match(personId(a), /^[0-9a-f]{12}$/);
  assert.notEqual(personId(a), personId(b));
  assert.equal(personDirectory(a), `张三~${personId(a)}`);
  names.ou_a = '张三丰';
  assert.equal(personId(chatEvent({ senderId: 'ou_a' })), personId(a), 'a new name, the same person');
  assert.equal(personId(mailEvent({ from: 'Bob@Example.com' })), personId(mailEvent({ from: 'bob@example.com' })));
  assert.notEqual(personId(chatEvent({ senderId: 'ou_a', source: 'telegram' })), personId(a), 'another channel, another identity');
});

test('the owner has one reserved directory that no one else can be given', () => {
  assert.equal(personDirectory(chatEvent({ senderId: 'ou_a' }), { owner: true }), OWNER_DIRECTORY);
  const lookalike = mailEvent({ from: 'x@example.com', name: 'owner' });
  assert.equal(personDirectory(lookalike), `owner~${personId(lookalike)}`);
  assert.deepEqual(directoryPerson(personDirectory(lookalike)), { id: personId(lookalike) });
  assert.deepEqual(directoryPerson('owner'), { owner: true });
  assert.equal(directoryPerson('张三'), null, 'a name-only directory is no one\'s');
  assert.equal(directoryPerson('张三~short'), null);
});

test('no person behind a bot or a senderless input; a name is one safe path segment', () => {
  assert.equal(personId(chatEvent({ senderId: 'cli_bot', senderType: 'bot' })), null);
  assert.equal(personDirectory(chatEvent({ senderId: 'cli_bot', senderType: 'app' })), null);
  assert.equal(personId({ source: 'timer', senderId: null }), null);
  const odd = mailEvent({ from: 'odd@example.com', name: '../a/b~c d\u0007' });
  const [name, id, ...rest] = personDirectory(odd).split('~');
  assert.match(name, /^[^/\\~.\s\u0000-\u001f]+$/);
  assert.deepEqual([id, rest], [personId(odd), []]);
  assert.equal(personDirectory(mailEvent({ from: 'dots@example.com', name: '...' })).split('~')[0], 'someone');
});

test('a topic is one file, the same however its case and spacing are written', () => {
  assert.equal(topicFile('Reply  language'), 'reply-language.md');
  assert.equal(topicFile(' reply_LANGUAGE '), 'reply-language.md');
  assert.equal(topicFile('回复/语言'), '回复语言.md');
  for (const topic of ['', '  ', '..', '///', null]) assert.equal(topicFile(topic), null);
});
