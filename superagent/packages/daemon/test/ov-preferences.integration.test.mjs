// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// openviking_remember against a real OpenViking: preferences land in the
// directory of the person who stated them, namesakes and the owner apart;
// recall shows each person only their own; a change replaces, a withdrawal
// removes; and OV's own search finds them. Opt in with
// OV_SUPERAGENT_OV_PREFERENCE_TEST_CONFIG naming the ovConfig of a NEW, disposable
// user on a test server: it writes synthetic preferences and leaves them for
// inspection. Never point it at an instance's real memory.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import { test } from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';
import { ActionService } from '../src/actions/service.mjs';
import { OVConnection, readOVConfig } from '../src/ov-connection.mjs';
import { personDirectory } from '../src/ov-person.mjs';
import { OVRecall } from '../src/ov-recall.mjs';
import { OVToolClient } from '../src/ov-tools.mjs';
import { registerNameResolver } from '../src/roster.mjs';
import { Store } from '../src/store.mjs';
import { fakeConnectors } from './action-fixture.mjs';
import { chatEvent } from './event-fixture.mjs';

const configPath = process.env.OV_SUPERAGENT_OV_PREFERENCE_TEST_CONFIG;
const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

test('live OV: each person\'s preferences stay theirs through store, change, withdrawal, recall and search', {
  skip: !configPath && 'set OV_SUPERAGENT_OV_PREFERENCE_TEST_CONFIG to a disposable OV user', timeout: 300_000,
}, async (t) => {
  const connection = new OVConnection(readOVConfig(configPath), { timeoutMs: 60_000 });
  const tree = async () => {
    try {
      const { result } = await connection.json(`/api/v1/fs/ls?uri=${encodeURIComponent('viking://~/memories/preferences')}&recursive=true&output=original`);
      return (result ?? []).filter((entry) => !entry.isDir && !entry.uri.split('/').pop().startsWith('.'))
        .map((entry) => entry.uri.split('/memories/preferences/')[1]).sort();
    } catch (error) {
      if (error.httpStatus === 404) return [];
      throw error;
    }
  };
  assert.deepEqual(await tree(), [], 'requires a user with no preferences yet');

  const names = { ou_owner: '刘维汉', ou_z1: '张三', ou_z2: '张三', ou_li: '李四' };
  registerNameResolver((chatId, id) => names[id] ?? null);
  const store = new Store(':memory:', logger);
  store.initializeCognition('codex', 'live');
  t.after(() => { registerNameResolver(null); store.close(); });
  const recall = new OVRecall({ configPath, logger, timeoutMs: 20_000 });
  const service = new ActionService({ config: {}, store, logger,
    connectors: fakeConnectors({ ids: ['lark'], owners: { lark: { id: 'ou_owner' } } }),
    ovTools: new OVToolClient({ configPath, timeoutMs: 60_000 }), onPreferencesChanged: () => recall.forgetPreferences() });
  const said = (senderId, messageId, content) => {
    const event = chatEvent({ senderId, messageId, chatId: 'oc_team', chatType: 'group', content });
    store.ingest(event);
    return event;
  };
  const remember = (event, input) => service.call('openviking_remember', { decision_id: event.eventId, ...input }, { authority: 'live' });

  const z1 = said('ou_z1', 'om_z1', '以后回复我请用法语');
  const z2 = said('ou_z2', 'om_z2', '以后回复我请用德语');
  const owner = said('ou_owner', 'om_owner', '以后在有外部人员的群里不要透露内部数据');
  const li = said('ou_li', 'om_li', '我手上私有化项目的进度先别跟别人说');
  await remember(z1, { topic: '回复语言', preference: '回复他时用法语' });
  await remember(z2, { topic: '回复语言', preference: '回复他时用德语' });
  await remember(owner, { topic: '信息披露', preference: '有外部人员的群里不透露内部数据' });
  await remember(li, { topic: '项目隐私', preference: '他的私有化项目进度不要告诉别人' });
  assert.deepEqual(await tree(), [
    `${personDirectory(li)}/项目隐私.md`, 'owner/信息披露.md',
    `${personDirectory(z1)}/回复语言.md`, `${personDirectory(z2)}/回复语言.md`,
  ].sort());

  const shown = (event, options) => recall.forEvent(event, options);
  const first = await shown(z1);
  assert.match(first, /<memory who="owner" type="preferences">\n## 信息披露\n有外部人员的群里不透露内部数据/);
  assert.match(first, /<memory who="sender" type="preferences">\n## 回复语言\n回复他时用法语/);
  assert.doesNotMatch(first, /德语|项目进度|MEMORY_FIELDS/);
  assert.match(await shown(z2), /<memory who="sender" type="preferences">\n## 回复语言\n回复他时用德语/);
  assert.doesNotMatch(await shown(owner, { owner: true }), /who="sender"/);

  // A change replaces the topic; a withdrawal removes it; the namesake keeps theirs.
  const changed = said('ou_z1', 'om_z1b', '改一下，以后用英语就行');
  await remember(changed, { topic: '回复语言', preference: '回复他时用英语，不再用法语' });
  assert.match(await shown(z1), /## 回复语言\n回复他时用英语，不再用法语/);
  const withdrawn = said('ou_z1', 'om_z1c', '语言那条取消吧，随意就好');
  await remember(withdrawn, { topic: '回复语言', forget: true });
  assert.doesNotMatch(await shown(z1), /who="sender"/);
  assert.match(await shown(z2), /回复他时用德语/);
  assert.deepEqual(await tree(), [`${personDirectory(li)}/项目隐私.md`, 'owner/信息披露.md', `${personDirectory(z2)}/回复语言.md`].sort());

  // OV indexes what the Core wrote: a search finds it, labelled with whose it is.
  const deadline = Date.now() + 90_000;
  let found = '';
  while (Date.now() < deadline && !found.includes(`preferences of ${personDirectory(li)}`)) {
    found = (await service.call('openviking_find', { query: '李四的私有化项目进度能告诉别人吗' }, { authority: 'live' })).text;
    if (!found.includes(`preferences of ${personDirectory(li)}`)) await delay(2_000);
  }
  assert.match(found, new RegExp(`preferences of ${personDirectory(li)}`));
  t.diagnostic(`preference tree: ${JSON.stringify(await tree())}`);
});
