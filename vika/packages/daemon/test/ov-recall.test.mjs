// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { Harness } from '../src/harness.mjs';
import { OVRecall } from '../src/ov-recall.mjs';
import { registerNameResolver } from '../src/roster.mjs';
import { Store } from '../src/store.mjs';
import { chatEvent, mailEvent } from './event-fixture.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

function configFile(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-recall-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const file = path.join(directory, 'ov.json');
  fs.writeFileSync(file, JSON.stringify({ url: 'http://ov.test', account: 'team', user: 'owner.asd' }));
  return file;
}

// An OV that answers context searches and profile reads, and records them.
function fakeOV({ entries = [], profiles = {}, delayMs = 0, fail = false } = {}) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    const call = { url, headers: init.headers, body: init.body ? JSON.parse(init.body) : null };
    calls.push(call);
    if (delayMs) await new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, delayMs);
      init.signal?.addEventListener('abort', () => { clearTimeout(timer); reject(init.signal.reason); });
    });
    if (fail) return Response.json({ status: 'error' }, { status: 500 });
    if (url.includes('/api/v1/content/read')) {
      const uri = decodeURIComponent(new URL(url).searchParams.get('uri'));
      return uri in profiles
        ? Response.json({ status: 'ok', result: profiles[uri] })
        : Response.json({ status: 'error' }, { status: 404 });
    }
    return Response.json({ status: 'ok', result: { entries, rendered: '', stats: {} } });
  };
  return { calls, fetchImpl };
}

const ENTRIES = [
  { uri: 'viking://user/owner.asd/peers/lark-ou_zhiheng/memories/preferences/review.md', category: 'preferences', origin: 'actor_peer', text: '评审建议用表格' },
  { uri: 'viking://user/owner.asd/memories/entities/vika.md', category: 'entities', origin: 'self', text: 'Vika 是 OV 的数字员工' },
  { uri: 'viking://user/owner.asd/peers/lark-ou_zuwei/memories/events/2026/11/03', category: 'events', origin: 'other_peer', text: '' },
  { uri: 'viking://user/owner.asd/resources/emails/abc/body.md', category: 'resources', origin: 'self', text: '' },
];

test('a line from a peer is searched as that peer, with its profile first and every memory saying whose it is', async (t) => {
  const ov = fakeOV({ entries: ENTRIES, profiles: {
    'viking://~/peers/lark-ou_zhiheng/memories/profile.md': '# 刘智恒\n- OV 负责人',
  } });
  registerNameResolver((chatId, id) => (chatId === 'oc-group' && id === 'ou_zuwei' ? '黄祖尉' : null));
  t.after(() => registerNameResolver(null));
  const recall = new OVRecall({ configPath: configFile(t), fetchImpl: ov.fetchImpl, logger });
  const block = await recall.forEvent(chatEvent({ senderId: 'ou_zhiheng', chatId: 'oc-group', messageId: 'om-9', content: '评审格式？' }));
  const search = ov.calls.find(({ url }) => url.endsWith('/api/v1/search/search'));
  assert.equal(search.headers['X-OpenViking-Actor-Peer'], 'lark-ou_zhiheng');
  assert.equal(search.headers['X-OpenViking-User'], 'owner.asd');
  assert.deepEqual(search.body, { query: '评审格式？', mode: 'context', score_threshold: 0.35, max_tokens: 1600 });
  // The profile read carries no actor peer, which would hide the others.
  assert.equal(ov.calls.find(({ url }) => url.includes('content/read')).headers['X-OpenViking-Actor-Peer'], undefined);
  assert.equal(block, [
    '[daemon recall msg=om-9: OpenViking memory that may bear on this line]',
    '<memory who="sender" type="profile">\n# 刘智恒\n- OV 负责人\n</memory>',
    `<memory who="sender" type="preferences" uri="${ENTRIES[0].uri}">\n评审建议用表格\n</memory>`,
    `<memory who="owner" type="entities" uri="${ENTRIES[1].uri}">\nVika 是 OV 的数字员工\n</memory>`,
    `<memory who="黄祖尉" type="events" uri="${ENTRIES[2].uri}" />`,
    `<memory who="owner" type="resources" uri="${ENTRIES[3].uri}" />`,
  ].join('\n'));
});

test('the owner is searched as the user itself; scope actor limits peers to the sender; a missing profile is remembered', async (t) => {
  const ov = fakeOV({ entries: ENTRIES.slice(1, 2) });
  const recall = new OVRecall({ configPath: configFile(t), fetchImpl: ov.fetchImpl, logger, scope: 'actor' });
  await recall.forEvent(chatEvent({ senderId: 'ou_owner', content: '我的会议' }), { owner: true });
  assert.equal(ov.calls.length, 1, 'no profile read for the owner');
  assert.equal(ov.calls[0].headers['X-OpenViking-Actor-Peer'], undefined);
  assert.equal(ov.calls[0].body.peer_scope, 'actor');
  await recall.forEvent(mailEvent({ from: 'Bob@Example.com', text: '周四开会？' }));
  await recall.forEvent(mailEvent({ from: 'bob@example.com', text: '周五呢？' }));
  const reads = ov.calls.filter(({ url }) => url.includes('content/read'));
  assert.equal(reads.length, 1, 'a missing profile is not asked for again at once');
  assert.match(decodeURIComponent(reads[0].url), /peers\/email-bob@example\.com\/memories\/profile\.md/);
});

test('recall never holds a line up for long or fails it', async (t) => {
  const slow = fakeOV({ entries: ENTRIES, delayMs: 5_000 });
  const recall = new OVRecall({ configPath: configFile(t), fetchImpl: slow.fetchImpl, logger, timeoutMs: 50 });
  const started = Date.now();
  assert.equal(await recall.forEvent(chatEvent({ content: '在吗' })), '');
  assert.ok(Date.now() - started < 1_000);
  const broken = new OVRecall({ configPath: configFile(t), fetchImpl: fakeOV({ fail: true }).fetchImpl, logger });
  assert.equal(await broken.forEvent(chatEvent({ content: '在吗' })), '');
  assert.equal(await broken.forEvent(chatEvent({ content: ' ' })), '', 'nothing to search for');
});

test('the dedup ledger is used once OV has the mirrored session, never before', async (t) => {
  const store = new Store(':memory:', logger);
  t.after(() => store.close());
  store.initializeCognition('codex', 'owner');
  let mirrored = null;
  store.ovSessionFor = () => mirrored;
  const ov = fakeOV({ entries: [] });
  const recall = new OVRecall({ configPath: configFile(t), fetchImpl: ov.fetchImpl, logger, store });
  await recall.forEvent(chatEvent({ content: '一一' }), { owner: true });
  assert.equal(ov.calls[0].body.session_id, undefined, 'not yet in OV');
  mirrored = { session_id: 'lark-s1', uri: 'viking://session/lark-s1' };
  await recall.forEvent(chatEvent({ content: '二二' }), { owner: true });
  assert.deepEqual(
    { session_id: ov.calls[1].body.session_id, dedup_turns: ov.calls[1].body.dedup_turns, query_expansion: ov.calls[1].body.query_expansion },
    { session_id: 'lark-s1', dedup_turns: 5, query_expansion: 'off' },
  );
});

test('the harness recalls only where the agent is likely to answer, and puts the block after the line', async (t) => {
  const store = new Store(':memory:', logger);
  store.initializeCognition('codex', 'owner');
  const delivered = [];
  const asked = [];
  const harness = new Harness({
    config: { maxEventBytes: 100_000, maxEventAgeMs: 60_000, ignoredSenderIds: new Set() }, store, logger, recordActivity() {},
    runtime: { snapshot: () => ({ session: { connected: true }, work: { turnInProgress: false } }),
      submit: (items) => { delivered.push(...items); return { accepted: true }; } },
    roster: { warm: async () => {} },
    recall: { forEvent: async (event, { owner }) => { asked.push([event.messageId, owner]); return `[daemon recall msg=${event.messageId}: …]`; } },
    isOwner: (event) => event.senderId === 'ou_owner',
    mentionTarget: (event) => (event.content.includes('@Vika') ? 'me' : null),
  });
  t.after(() => { harness.stop(); store.close(); });
  for (const fields of [
    { messageId: 'om-dm', chatType: 'p2p', senderId: 'ou_owner' },
    { messageId: 'om-mention', chatType: 'group', content: '@Vika 看下' },
    { messageId: 'om-chatter', chatType: 'group', content: '午饭吃啥' },
    { messageId: 'om-bot', chatType: 'p2p', senderType: 'bot', senderId: 'cli_bot' },
  ]) harness.handleConnectorEvent(chatEvent(fields).envelope);
  await harness.ingressBarrier;
  await harness.groupBurst.flushAll();
  // Group chatter not addressed to the agent and another bot's line get none.
  assert.deepEqual(asked, [['om-dm', true], ['om-mention', false]]);
  const dm = delivered.find((item) => item.text.includes('msg=om-dm'));
  assert.match(dm.text, /msg=om-dm[^\n]*\] hello\n\[daemon recall msg=om-dm: …\]$/);
  assert.ok(delivered.some((item) => item.text.includes('午饭吃啥') && !item.text.includes('daemon recall')));
});
