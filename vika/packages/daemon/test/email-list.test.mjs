// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { decodeMailboxName, listMailboxes, parseListResponse, selectMailboxes } from '../src/email/list-mailboxes.mjs';
import { mailboxWireName } from '../src/email/mailbox.mjs';

const entry = (name, delimiter = '/', flags = []) => ({ name, delimiter, flags });

test('LIST parses quoted names, escaped strings, literals and unsolicited responses', () => {
  assert.deepEqual(parseListResponse([String.raw`* LIST (\HasNoChildren) "/" "Work \"quoted\""`]),
    entry('Work "quoted"', '/', ['\\hasnochildren']));
  assert.deepEqual(parseListResponse(['* LIST () NIL {10}', Buffer.from(' A\\folder '), '']), entry(' A\\folder ', null));
  assert.deepEqual(parseListResponse(['* LIST (\\Noselect) "." Archive']), entry('Archive', '.', ['\\noselect']));
  assert.deepEqual(parseListResponse(['* LIST () "&" inbox']), entry('INBOX', '&'));
  assert.equal(parseListResponse(['* 3 EXISTS']), null);
  assert.equal(parseListResponse(['* OK hello']), null);
});

test('mailbox decoding handles modified UTF-7 and byte-preserving rev2 UTF-8', () => {
  for (const name of ['台北', '邮件 & 工作', 'emoji 📬', '\ufefffolder']) {
    assert.equal(decodeMailboxName(mailboxWireName(name)), name);
    const wire = Buffer.from(name);
    assert.equal(decodeMailboxName(wire, { utf8: true }), name);
    assert.equal(parseListResponse([`* LIST () "/" "${wire.toString('latin1')}"`], { utf8: true }).name, name);
    assert.equal(parseListResponse(['* LIST () "/" {999}', wire, ''], { utf8: true }).name, name);
  }
  assert.equal(decodeMailboxName('&-'), '&');
  for (const bad of ['&unterminated', '&A-', '&AA=-', '&/w-', '&2AA-']) {
    assert.throws(() => decodeMailboxName(bad));
  }
  assert.throws(() => decodeMailboxName(Buffer.from([0xff]), { utf8: true }));
  assert.throws(() => parseListResponse(['* LIST () "/" ""']));
});

test('selection uses hierarchy boundaries, skips nonselectable folders, and preserves order', () => {
  const entries = [entry('INBOX'), entry('Work', '/', ['\\Noselect']), entry('Work/Inbox'),
    entry('Work/Trash'), entry('Work/Trash/Old'), entry('Work/Trashcan'), entry('Workshops'),
    entry('Work/Gone', '/', ['\\NonExistent']), entry('Work/Inbox'), entry('work/lower')];
  assert.deepEqual(selectMailboxes(entries, { roots: ['Work'], exclude: ['Work/Trash'] }).map((box) => box.name),
    ['Work/Inbox', 'Work/Trashcan']);
  assert.deepEqual(selectMailboxes(entries, { roots: ['inbox'] }).map((box) => box.name), ['INBOX']);
  assert.deepEqual(selectMailboxes(entries, { roots: ['Missing'] }), []);
  assert.equal(selectMailboxes(entries).length, 7);
});

test('dot and NIL hierarchies and absent parents have exact subtree semantics', () => {
  const entries = [entry('Root', '.'), entry('Root.Child', '.'), entry('Rootish.Child', '.'),
    entry('Flat', null), entry('Flat.Child', null), entry('Absent/Child'), entry('Absent/Trash')];
  assert.deepEqual(selectMailboxes(entries, { roots: ['Root'] }).map((box) => box.name), ['Root', 'Root.Child']);
  assert.deepEqual(selectMailboxes(entries, { exclude: ['Flat'] }).map((box) => box.name),
    ['Root', 'Root.Child', 'Rootish.Child', 'Flat.Child', 'Absent/Child', 'Absent/Trash']);
  assert.deepEqual(selectMailboxes(entries, { roots: ['Absent'], exclude: ['Absent/Trash'] }).map((box) => box.name), ['Absent/Child']);
});

test('discovery uses LIST and fails malformed refreshes instead of returning partial snapshots', async () => {
  const calls = [];
  const imap = { capabilities: new Set(['IMAP4REV2']), command: async (...args) => {
    calls.push(args);
    return { untagged: [['* OK ignored'], ['* LIST () "/" {6}', Buffer.from('工作'), '']] };
  } };
  assert.deepEqual(await listMailboxes(imap), [entry('工作')]);
  assert.deepEqual(calls, [['LIST', '"" "*"']]);
  imap.command = async () => ({ untagged: [['* LIST () "/" Inbox'], ['* LIST broken']] });
  await assert.rejects(listMailboxes(imap), /Invalid IMAP LIST/);
});
