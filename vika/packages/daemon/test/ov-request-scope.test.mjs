// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { ActionService } from '../src/actions/service.mjs';
import { normalizeEvent } from '../src/event.mjs';
import { privateMailFilter } from '../src/ov-path.mjs';
import { OVSessionClient } from '../src/ov-session-context.mjs';
import { OVToolClient } from '../src/ov-tools.mjs';
import { Store } from '../src/store.mjs';
import { fakeConnectors } from './action-fixture.mjs';
import { chatEvent, mailEvent } from './event-fixture.mjs';
import { fakeOV } from './ov-server-fixture.mjs';

const ROOT = 'viking://user/actual-owner/resources';
const MAIL = `${ROOT}/emails/12345678-1-1/body.md`;
const OTHER_MAIL = `${ROOT}/emails/12345678-1-2/body.md`;
const DOC = `${ROOT}/inbox/doc.md`;
const OWNER = { decision_id: 'message:om_owner' };
const OTHER = { decision_id: 'message:om_other' };

async function setup(t) {
  const ov = await fakeOV(t, { apiKeys: { 'instance-key': 'actual-owner' } });
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ov-request-'));
  const configPath = path.join(directory, 'ov.json');
  fs.writeFileSync(configPath, JSON.stringify({ url: ov.url, api_key: 'instance-key', user: 'stale-config-owner' }));
  const logs = [];
  const logger = { debug() {}, info(message, fields) { logs.push({ message, ...fields }); }, warn() {}, error() {} };
  const store = new Store(':memory:', logger);
  t.after(() => { store.close(); fs.rmSync(directory, { recursive: true, force: true }); });
  store.initializeCognition('codex', 'runtime');
  const owner = store.ingest(chatEvent({ messageId: 'om_owner', senderId: 'ou_owner', content: 'Find my mail' })).inbound;
  const other = store.ingest(chatEvent({ messageId: 'om_other', senderId: 'ou_other', content: 'What do you know?' })).inbound;
  const tools = new OVToolClient({ configPath });
  const service = new ActionService({ config: {}, store, logger,
    connectors: fakeConnectors({ ids: ['lark', 'lark-second', 'email'], owners: { lark: { id: 'ou_owner' } } }),
    ovTools: tools, ovClient: new OVSessionClient(configPath) });
  const call = (name, input) => service.call(name, input, { authority: 'runtime' });
  ov.state.files.set(MAIL, 'PRIVATE first mail');
  ov.state.files.set(OTHER_MAIL, 'PRIVATE other mail');
  ov.state.files.set(DOC, 'An ordinary resource');
  ov.state.searchResult = { resources: [
    { uri: MAIL, abstract: 'PRIVATE first mail' },
    { uri: 'viking://~/resources//emails/12345678-1-2/body.md', abstract: 'PRIVATE alias' },
    { uri: `${ROOT}/.abstract.md`, abstract: 'PRIVATE aggregate' },
    { uri: ROOT, abstract: 'PRIVATE directory aggregate' },
    { uri: DOC, abstract: 'An ordinary resource' },
  ], total: 5 };
  return { ov, store, tools, call, logs, owner, other };
}

test('each lookup resolves its request in a mixed turn; missing, unknown and ambiguous attribution is refused', async (t) => {
  const { call, ov, store, logs, owner, other } = await setup(t);
  await assert.rejects(call('openviking_find', { query: 'x' }), /decision_id is required/);
  await assert.rejects(call('openviking_find', { query: 'x', decision_id: 'message:missing' }), { code: 'unknown_decision' });
  await assert.rejects(call('openviking_find', { query: 'x', ...OTHER, allowPrivateMail: true }), { code: 'invalid_input' });
  assert.equal(ov.state.requests.length, 0);
  for (const tool of ['openviking_find', 'openviking_search']) {
    const result = await call(tool, { query: 'mail', ...OTHER });
    assert.deepEqual(result.data.hits.map(({ uri }) => uri), [DOC]);
    assert.equal(result.data.total, 1);
    assert.doesNotMatch(JSON.stringify(result), /PRIVATE|emails/);
    const search = ov.state.requests.at(-1);
    assert.deepEqual(search.body.filter, privateMailFilter('actual-owner'));
    assert.equal(search.body.decision_id, undefined, 'attribution is local, not an OV parameter');
    const own = await call(tool, { query: 'mail', ...OWNER });
    assert.match(own.text, /PRIVATE first mail/);
    assert.equal(ov.state.requests.at(-1).body.filter, undefined);
  }
  assert.ok(logs.some((row) => row.decisionSeq === owner.seq && row.privateMailAllowed === true));
  assert.ok(logs.some((row) => row.decisionSeq === other.seq && row.privateMailAllowed === false));
  store.ingest(chatEvent({ messageId: 'om_owner', connectorInstance: 'lark-second', senderId: 'ou_owner' }));
  await assert.rejects(call('openviking_find', { query: 'mail', ...OWNER }), { code: 'decision_ambiguous' });
  const secondInstance = await call('openviking_find', { query: 'mail', ...OWNER, decision_instance: 'lark-second' });
  assert.doesNotMatch(secondInstance.text, /PRIVATE/);
  assert.match((await call('openviking_find', { query: 'mail', ...OWNER, decision_instance: 'lark' })).text, /PRIVATE/);
  assert.equal(store.db.prepare('SELECT count(*) AS n FROM decisions').get().n, 0);
  assert.equal(store.db.prepare('SELECT count(*) AS n FROM outbox').get().n, 0);
});

test('all direct mail paths and parent summaries are denied, and recursive listings filter both text and structured output', async (t) => {
  const { call, ov } = await setup(t);
  for (const uri of [MAIL, MAIL.replace('viking://user/actual-owner', 'viking://~'),
    MAIL.replace('/resources/', '//resources//'), `${ROOT}/emails`, `${ROOT}/.overview.md`]) {
    const before = ov.state.requests.length;
    for (const tool of ['openviking_read', 'openviking_list']) {
      await assert.rejects(call(tool, { uri, ...OTHER }), { code: 'private_mail_denied' });
    }
    await assert.rejects(call('openviking_find', { query: 'x', target_uri: uri, ...OTHER }), { code: 'private_mail_denied' });
    assert.equal(ov.state.requests.length, before, 'refused before any network request');
  }
  assert.match((await call('openviking_read', { uri: MAIL, ...OWNER })).text, /PRIVATE first mail/);
  assert.match((await call('openviking_read', { uri: DOC, ...OTHER })).text, /ordinary resource/);
  ov.state.listings.set(ROOT, { entries: [
    { uri: MAIL, name: 'PRIVATE title', isDir: false },
    { uri: `${ROOT}/.abstract.md`, name: 'PRIVATE summary', isDir: false },
    { uri: DOC, name: 'doc.md', isDir: false },
  ], has_more: true });
  const hidden = await call('openviking_list', { uri: ROOT, recursive: true, limit: 2, ...OTHER });
  assert.deepEqual(hidden.data.entries, []);
  assert.equal(hidden.data.next_offset, 2, 'advance past filtered entries');
  const next = await call('openviking_list', { uri: ROOT, recursive: true, offset: hidden.data.next_offset, ...OTHER });
  assert.deepEqual(next.data.entries.map(({ uri }) => uri), [DOC]);
  assert.doesNotMatch(JSON.stringify(next), /PRIVATE/);
  ov.state.listings.set('viking://user/actual-owner', { entries: [{ uri: ROOT, abstract: 'PRIVATE directory abstract', isDir: true }] });
  assert.doesNotMatch(JSON.stringify(await call('openviking_list', { uri: 'viking://~', ...OTHER })), /PRIVATE/);
});

test('an incoming mail authorizes inspection of that mail only, including attachments, regardless of its claimed sender', async (t) => {
  const { call, store, ov } = await setup(t);
  const envelope = mailEvent({ from: 'ou_owner', name: 'Owner' }).envelope;
  envelope.message.id = 'mail:12345678:1:1';
  envelope.data.archive = { body: MAIL, attachments: [{ id: '01', uri: `${ROOT}/emails/12345678-1-1/attachments/01-file/full` }] };
  const mail = normalizeEvent(envelope);
  store.ingest(mail);
  ov.state.files.set(`${ROOT}/emails/12345678-1-1/attachments/01-file/full/doc.md`, 'PRIVATE attachment');
  const request = { decision_id: mail.eventId };
  assert.match((await call('read_email', { message_id: mail.messageId, ...request })).text, /PRIVATE first mail/);
  assert.match((await call('read_email', { message_id: mail.messageId, attachment: '01', ...request })).text, /PRIVATE attachment/);
  await assert.rejects(call('read_email', { message_id: 'mail:12345678:1:2', ...request }), { code: 'private_mail_denied' });
  await assert.rejects(call('read_email', { uri: OTHER_MAIL, ...request }), { code: 'private_mail_denied' });
  await assert.rejects(call('read_email', { uri: MAIL, ...OTHER }), { code: 'private_mail_denied' });
  await assert.rejects(call('openviking_read', { uri: MAIL, ...request }), { code: 'private_mail_denied' });
  assert.match((await call('read_email', { uri: OTHER_MAIL, ...OWNER })).text, /PRIVATE other mail/);
});

test('resource writes normalize slashes before protecting daemon trees, and reject ambiguous paths before upload', async (t) => {
  const { tools, ov } = await setup(t);
  for (const prefix of ['viking://~/', 'viking://user/actual-owner/']) {
    for (const subtree of ['resources/emails', 'resources/meetings', 'resources/vika', 'memories', 'sessions', 'peers', 'skills']) {
      for (const field of ['to', 'parent']) {
        const uri = `${prefix}/${subtree.replaceAll('/', '//')}//replacement`;
        await assert.rejects(tools.addResource({ file_path: 'nonexistent', [field]: uri }), { code: 'openviking_reserved_target' });
      }
    }
    for (const tail of ['resources/./emails/x', 'resources/inbox/../emails/x', 'resources/%65mails/x', 'resources\\emails/x']) {
      await assert.rejects(tools.addResource({ file_path: 'nonexistent', parent: `${prefix}${tail}` }), { code: 'invalid_input' });
    }
    await assert.rejects(tools.addResource({ url: 'https://example.com/x', to: `${prefix}resources` }), { code: 'openviking_reserved_target' });
  }
  assert.equal(ov.state.requests.length, 0);
  await tools.addResource({ url: 'https://example.com/x', parent: 'viking://~//resources//inbox/' });
  assert.equal(ov.state.requests.at(-1).body.parent, 'viking://~/resources/inbox');
});
