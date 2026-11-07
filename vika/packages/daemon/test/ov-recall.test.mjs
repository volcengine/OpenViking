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

// An OV that answers context searches, directory listings and reads, and
// records them. `files` maps a viking:// uri to its text; a directory lists
// the files and directories directly under it.
function fakeOV({ entries = [], files = {}, delayMs = 0, fail = false } = {}) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    const call = { url, headers: init.headers, body: init.body ? JSON.parse(init.body) : null };
    calls.push(call);
    if (delayMs) await new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, delayMs);
      init.signal?.addEventListener('abort', () => { clearTimeout(timer); reject(init.signal.reason); });
    });
    if (fail) return Response.json({ status: 'error' }, { status: 500 });
    const uri = new URL(url).searchParams.get('uri');
    if (url.includes('/api/v1/content/read')) {
      return uri in files ? Response.json({ status: 'ok', result: files[uri] }) : Response.json({ status: 'error' }, { status: 404 });
    }
    if (url.includes('/api/v1/fs/ls')) {
      const prefix = `${uri.replace('viking://~/', 'viking://user/owner.asd/')}/`;
      const children = new Map();
      for (const file of Object.keys(files)) {
        const full = file.replace('viking://~/', 'viking://user/owner.asd/');
        if (!full.startsWith(prefix)) continue;
        const [first, ...rest] = full.slice(prefix.length).split('/');
        children.set(`${prefix}${first}`, rest.length > 0);
      }
      if (!children.size) return Response.json({ status: 'error' }, { status: 404 });
      return Response.json({ status: 'ok', result: [...children].map(([child, isDir]) => ({ uri: child, isDir })) });
    }
    return Response.json({ status: 'ok', result: { entries, rendered: '', stats: {} } });
  };
  return { calls, fetchImpl };
}

const ROOT = 'viking://user/owner.asd/memories';
const FILES = {
  [`${ROOT}/preferences/刘维汉/信息披露.md`]: '- 外部群里不透露公司内部信息',
  [`${ROOT}/preferences/刘智恒/回复风格.md`]: '- 回复先给结论',
  [`${ROOT}/preferences/刘智恒/评审格式.md`]: '- 评审建议用表格',
  [`${ROOT}/preferences/bob smith/语言.md`]: '- 用英文回复',
  [`${ROOT}/profile.md`]: '# 刘维汉\n- OV 团队\n# 刘智恒\n- OV 负责人',
};
// The files above, read through the ~ alias the recall uses.
const withAlias = (files) => Object.fromEntries(Object.entries(files).flatMap(([uri, text]) => [[uri, text],
  [uri.replace('viking://user/owner.asd/', 'viking://~/'), text]]));

const ENTRIES = [
  { uri: `${ROOT}/preferences/黄祖尉/称呼.md`, category: 'preferences', text: '叫他祖尉' },
  { uri: `${ROOT}/entities/vika.md`, category: 'entities', text: 'Vika 是 OV 的数字员工' },
  { uri: `${ROOT}/events/2026/11/03/发布.md`, category: 'events', text: '' },
];

// A store that knows the owner's newest line, and the mirrored session.
const ownerStore = (session = null) => ({
  latestOwnerEnvelope: () => chatEvent({ senderId: 'ou_owner', chatId: 'oc-group' }).envelope,
  ovSessionFor: () => session,
  getCognitionState: () => ({ epoch: 1 }),
});

function names(t) {
  const known = { ou_owner: '刘维汉', ou_zhiheng: '刘智恒', ou_zuwei: '黄祖尉' };
  registerNameResolver((chatId, id) => known[id] ?? null);
  t.after(() => registerNameResolver(null));
}

test('a line from someone else shows the owner\'s standing preferences, the sender\'s, and their profile; found memories say whose', async (t) => {
  names(t);
  const ov = fakeOV({ entries: ENTRIES, files: withAlias(FILES) });
  const recall = new OVRecall({ configPath: configFile(t), fetchImpl: ov.fetchImpl, logger, store: ownerStore(), autoRecall: true });
  const block = await recall.forEvent(chatEvent({ senderId: 'ou_zhiheng', chatId: 'oc-group', messageId: 'om-9', content: '评审格式？' }));
  const search = ov.calls.find(({ url }) => url.endsWith('/api/v1/search/search'));
  assert.equal(search.headers['X-OpenViking-User'], 'owner.asd');
  for (const call of ov.calls) assert.equal(call.headers['X-OpenViking-Actor-Peer'], undefined, 'no peer, ever');
  assert.deepEqual(search.body, { query: '评审格式？', mode: 'context', score_threshold: 0.35, max_tokens: 1600 });
  assert.equal(block, [
    '[daemon recall msg=om-9: OpenViking memory that may bear on this line]',
    '<memory who="owner" type="preferences">\n## 信息披露\n- 外部群里不透露公司内部信息\n</memory>',
    '<memory who="sender" type="preferences">\n## 回复风格\n- 回复先给结论\n\n## 评审格式\n- 评审建议用表格\n</memory>',
    '<memory who="sender" type="profile">\n- OV 负责人\n</memory>',
    `<memory who="黄祖尉" type="preferences" uri="${ENTRIES[0].uri}">\n叫他祖尉\n</memory>`,
    `<memory type="entities" uri="${ENTRIES[1].uri}">\nVika 是 OV 的数字员工\n</memory>`,
    `<memory type="events" uri="${ENTRIES[2].uri}" />`,
  ].join('\n'));
});

test('the owner\'s own line shows the owner\'s preferences once and no profile; the line search runs only when opted in', async (t) => {
  names(t);
  const ov = fakeOV({ entries: ENTRIES, files: withAlias(FILES) });
  const recall = new OVRecall({ configPath: configFile(t), fetchImpl: ov.fetchImpl, logger, store: ownerStore() });
  const block = await recall.forEvent(chatEvent({ senderId: 'ou_owner', chatId: 'oc-group', messageId: 'om-1', content: '我的会议' }), { owner: true });
  assert.equal(block, '[daemon recall msg=om-1: OpenViking memory that may bear on this line]\n'
    + '<memory who="owner" type="preferences">\n## 信息披露\n- 外部群里不透露公司内部信息\n</memory>');
  assert.equal(ov.calls.some(({ url }) => url.includes('/search/')), false, 'no search without ovAutoRecall');
  assert.equal(ov.calls.some(({ url }) => url.includes('profile.md')), false);
});

test('a person\'s directory matches across case, spaces and underscores; a missing one is remembered a while', async (t) => {
  const ov = fakeOV({ files: withAlias(FILES) });
  const recall = new OVRecall({ configPath: configFile(t), fetchImpl: ov.fetchImpl, logger });
  const block = await recall.forEvent(mailEvent({ from: 'bob@example.com', name: 'Bob Smith', text: '周四开会？' }));
  assert.match(block, /<memory who="sender" type="preferences">\n## 语言\n- 用英文回复\n<\/memory>/);
  // The people listing and profile.md are read once and kept a while: a
  // sender without preferences costs no request again.
  const before = ov.calls.length;
  assert.equal(await recall.forEvent(mailEvent({ from: 'carol@example.com', name: 'Carol', text: '周五呢？' })), '');
  assert.equal(await recall.forEvent(mailEvent({ from: 'carol@example.com', name: 'Carol', text: '下周呢？' })), '');
  assert.equal(ov.calls.length, before, 'nothing is asked for again at once');
});

test('recall never holds a line up for long or fails it, a broken config included', async (t) => {
  const slow = fakeOV({ entries: ENTRIES, files: withAlias(FILES), delayMs: 5_000 });
  const recall = new OVRecall({ configPath: configFile(t), fetchImpl: slow.fetchImpl, logger, timeoutMs: 50, autoRecall: true });
  const started = Date.now();
  assert.equal(await recall.forEvent(chatEvent({ content: '在吗' })), '');
  assert.ok(Date.now() - started < 1_000);
  const broken = new OVRecall({ configPath: configFile(t), fetchImpl: fakeOV({ fail: true }).fetchImpl, logger, autoRecall: true });
  assert.equal(await broken.forEvent(chatEvent({ content: '在吗' })), '');
  assert.equal(await broken.forEvent(chatEvent({ content: ' ' })), '', 'nothing to search for');
  const unconfigured = new OVRecall({ configPath: path.join(os.tmpdir(), 'no-such-ov.json'), logger, autoRecall: true });
  assert.equal(await unconfigured.forEvent(chatEvent({ content: '在吗' })), '');
});

test('the dedup ledger is used once OV has the mirrored session, never before', async (t) => {
  const store = new Store(':memory:', logger);
  t.after(() => store.close());
  store.initializeCognition('codex', 'owner');
  let mirrored = null;
  store.ovSessionFor = () => mirrored;
  const ov = fakeOV({ entries: [] });
  const recall = new OVRecall({ configPath: configFile(t), fetchImpl: ov.fetchImpl, logger, store, autoRecall: true });
  const searches = () => ov.calls.filter(({ url }) => url.includes('/search/search'));
  await recall.forEvent(chatEvent({ content: '一一' }), { owner: true });
  assert.equal(searches()[0].body.session_id, undefined, 'not yet in OV');
  mirrored = { session_id: 'lark-s1', uri: 'viking://session/lark-s1' };
  await recall.forEvent(chatEvent({ content: '二二' }), { owner: true });
  const [, second] = searches();
  assert.deepEqual(
    { session_id: second.body.session_id, dedup_turns: second.body.dedup_turns, query_expansion: second.body.query_expansion },
    { session_id: 'lark-s1', dedup_turns: 5, query_expansion: 'off' },
  );
});

test('the owner\'s name comes from the newest line the owner sent', async (t) => {
  names(t);
  const store = new Store(':memory:', logger);
  t.after(() => store.close());
  store.initializeCognition('codex', 'owner');
  assert.equal(store.latestOwnerEnvelope(), null);
  store.ingest(chatEvent({ senderId: 'ou_zhiheng', messageId: 'om-a', chatId: 'oc-group' }), null, Date.now(), { owner: false });
  store.ingest(chatEvent({ senderId: 'ou_owner', messageId: 'om-b', chatId: 'oc-group' }), null, Date.now(), { owner: true });
  store.ingest(chatEvent({ senderId: 'ou_zuwei', messageId: 'om-c', chatId: 'oc-group' }), null, Date.now(), { owner: false });
  const recall = new OVRecall({ configPath: configFile(t), fetchImpl: fakeOV().fetchImpl, logger, store });
  assert.equal(recall.ownerName(), '刘维汉');
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
