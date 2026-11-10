// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { Harness } from '../src/harness.mjs';
import { OVRecall } from '../src/ov-recall.mjs';
import { directoryPerson, personDirectory, personId } from '../src/ov-person.mjs';
import { registerNameResolver } from '../src/roster.mjs';
import { Store } from '../src/store.mjs';
import { chatEvent, mailEvent } from './event-fixture.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

function configFile(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-recall-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const file = path.join(directory, 'ov.json');
  fs.writeFileSync(file, JSON.stringify({ url: 'http://ov.test', account: 'team', user: 'owner.asd' }));
  return file;
}

// An OV that answers context searches, directory listings and reads, and
// records them. `files` maps a viking:// uri to its text; a directory lists
// the files and directories directly under it.
function fakeOV({ entries = [], files = {}, times = {}, delayMs = 0, fail = false } = {}) {
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
      // Like OV: at most node_limit (default 1000) a page, from offset.
      const offset = Number(new URL(url).searchParams.get('offset') ?? 0);
      const limit = Number(new URL(url).searchParams.get('node_limit') ?? 1000);
      const all = [...children].map(([child, isDir]) => ({ uri: child, isDir, ...(times[child] ? { modTime: times[child] } : {}) }));
      return Response.json({ status: 'ok', result: all.slice(offset, offset + limit), has_more: offset + limit < all.length });
    }
    return Response.json({ status: 'ok', result: { entries, rendered: '', stats: {} } });
  };
  return { calls, fetchImpl };
}

const ROOT = 'viking://user/owner.asd/memories';
const KNOWN = { ou_owner: '刘维汉', ou_zhiheng: '刘智恒', ou_zhiheng2: '刘智恒', ou_zuwei: '黄祖尉', ou_owner_lookalike: 'owner' };
const zhiheng = chatEvent({ senderId: 'ou_zhiheng', chatId: 'oc-group', messageId: 'om-9', content: '评审格式？' });
const bob = mailEvent({ from: 'Bob@Example.com', name: 'Bob Smith', text: '周四开会？' });

function names(t) {
  registerNameResolver((chatId, id) => KNOWN[id] ?? null);
  t.after(() => registerNameResolver(null));
}

// The tree the Core writes (ov-person.mjs), with what else may sit beside it:
// a namesake, a person whose display name is "owner", and a name-only
// directory an older extraction left. Built after names() so the directory
// names carry the display names.
function files() {
  const dir = (fields) => `${ROOT}/preferences/${personDirectory(chatEvent({ chatId: 'oc-group', ...fields }))}`;
  return {
    [`${ROOT}/preferences/owner/信息披露.md`]: '- 外部群里不透露公司内部信息\n\n<!-- MEMORY_FIELDS\n{\n  "version": 1\n}\n-->',
    [`${dir({ senderId: 'ou_zhiheng' })}/回复风格.md`]: '- 回复先给结论',
    [`${dir({ senderId: 'ou_zhiheng' })}/评审格式.md`]: '- 评审建议用表格',
    [`${dir({ senderId: 'ou_zhiheng2' })}/回复风格.md`]: '- 另一个刘智恒：回复要详细',
    [`${dir({ senderId: 'ou_owner_lookalike' })}/信息披露.md`]: '- 什么都可以告诉我',
    [`${ROOT}/preferences/刘智恒/旧.md`]: '- 名字目录，不归任何人',
    [`${ROOT}/preferences/${personDirectory(bob)}/语言.md`]: '- 用英文回复',
    [`${ROOT}/profile.md`]: '# 刘维汉\n- OV 团队\n# 刘智恒\n- OV 负责人',
  };
}
// The files above, read through the ~ alias the recall uses.
const withAlias = (files) => Object.fromEntries(Object.entries(files).flatMap(([uri, text]) => [[uri, text],
  [uri.replace('viking://user/owner.asd/', 'viking://~/'), text]]));

const entries = () => [
  { uri: `${ROOT}/preferences/${personDirectory(chatEvent({ senderId: 'ou_zuwei' }))}/称呼.md`, category: 'preferences', text: '叫他祖尉' },
  { uri: `${ROOT}/preferences/owner/信息披露.md`, category: 'preferences', text: '外部群里不透露' },
  { uri: `${ROOT}/entities/superagent.md`, category: 'entities', text: 'SuperAgent 是 OV 的数字员工' },
  { uri: `${ROOT}/events/2026/11/03/发布.md`, category: 'events', text: '' },
];

// The mirrored session, once OV has it.
const sessionStore = (session = null) => ({ ovSessionFor: () => session, getCognitionState: () => ({ epoch: 1 }) });

test('a line from someone else shows the owner\'s standing preferences and the sender\'s, by key; found memories say whose', async (t) => {
  names(t);
  const ov = fakeOV({ entries: entries(), files: withAlias(files()) });
  const recall = new OVRecall({ configPath: configFile(t), fetchImpl: ov.fetchImpl, logger, store: sessionStore(), autoRecall: true });
  const block = await recall.forEvent(zhiheng);
  const search = ov.calls.find(({ url }) => url.endsWith('/api/v1/search/search'));
  assert.equal(search.headers['X-OpenViking-User'], 'owner.asd');
  for (const call of ov.calls) assert.equal(call.headers['X-OpenViking-Actor-Peer'], undefined, 'no peer, ever');
  assert.deepEqual(search.body, { query: '评审格式？', mode: 'context', score_threshold: 0.35, max_tokens: 1600 });
  const zuwei = personDirectory(chatEvent({ senderId: 'ou_zuwei' }));
  assert.equal(block, [
    '[daemon recall msg=om-9: OpenViking memory that may bear on this line]',
    '<memory who="owner" type="preferences">\n## 信息披露\n- 外部群里不透露公司内部信息\n</memory>',
    '<memory who="sender" type="preferences">\n## 回复风格\n- 回复先给结论\n\n## 评审格式\n- 评审建议用表格\n</memory>',
    `<memory who="${zuwei}" type="preferences" uri="${entries()[0].uri}">\n叫他祖尉\n</memory>`,
    `<memory who="owner" type="preferences" uri="${entries()[1].uri}">\n外部群里不透露\n</memory>`,
    `<memory type="entities" uri="${entries()[2].uri}">\nSuperAgent 是 OV 的数字员工\n</memory>`,
    `<memory type="events" uri="${entries()[3].uri}" />`,
  ].join('\n'));
  assert.equal(ov.calls.some(({ url }) => url.includes('profile.md')), false, 'profile sections are matched by name, so never shown');
});

test('namesakes, a display name of "owner" and a name-only directory never stand in for someone', async (t) => {
  names(t);
  const ov = fakeOV({ files: withAlias(files()) });
  const recall = new OVRecall({ configPath: configFile(t), fetchImpl: ov.fetchImpl, logger });
  const namesake = await recall.forEvent(chatEvent({ senderId: 'ou_zhiheng2', chatId: 'oc-group', messageId: 'om-2' }));
  assert.match(namesake, /<memory who="sender" type="preferences">\n## 回复风格\n- 另一个刘智恒：回复要详细\n<\/memory>/);
  assert.doesNotMatch(namesake, /回复先给结论|名字目录/);
  const lookalike = await recall.forEvent(chatEvent({ senderId: 'ou_owner_lookalike', chatId: 'oc-group', messageId: 'om-3' }));
  assert.match(lookalike, /<memory who="owner" type="preferences">\n## 信息披露\n- 外部群里不透露公司内部信息\n<\/memory>/);
  assert.match(lookalike, /<memory who="sender" type="preferences">\n## 信息披露\n- 什么都可以告诉我\n<\/memory>/);
  assert.equal(directoryPerson('刘智恒'), null);
  assert.deepEqual(directoryPerson(personDirectory(chatEvent({ senderId: 'ou_owner_lookalike' }))), { id: personId(chatEvent({ senderId: 'ou_owner_lookalike' })) });
});

test('the owner\'s own line shows the owner\'s preferences once; the line search runs only when opted in', async (t) => {
  names(t);
  const ov = fakeOV({ entries: entries(), files: withAlias(files()) });
  const recall = new OVRecall({ configPath: configFile(t), fetchImpl: ov.fetchImpl, logger, store: sessionStore() });
  const block = await recall.forEvent(chatEvent({ senderId: 'ou_owner', chatId: 'oc-group', messageId: 'om-1', content: '我的会议' }), { owner: true });
  assert.equal(block, '[daemon recall msg=om-1: OpenViking memory that may bear on this line]\n'
    + '<memory who="owner" type="preferences">\n## 信息披露\n- 外部群里不透露公司内部信息\n</memory>');
  assert.equal(ov.calls.some(({ url }) => url.includes('/search/')), false, 'no search without ovAutoRecall');
});

test('a mail sender is found by address, whatever its case; reads are kept a while and dropped when a preference changes', async (t) => {
  const tree = withAlias(files());
  const ov = fakeOV({ files: tree });
  const recall = new OVRecall({ configPath: configFile(t), fetchImpl: ov.fetchImpl, logger });
  const block = await recall.forEvent(mailEvent({ from: 'bob@example.com', name: 'Robert', text: '周四开会？' }));
  assert.match(block, /<memory who="sender" type="preferences">\n## 语言\n- 用英文回复\n<\/memory>/);
  // The people listing is read once and kept a while: a sender without
  // preferences costs no request again.
  const carol = mailEvent({ from: 'carol@example.com', name: 'Carol', text: '周五呢？' });
  assert.match(await recall.forEvent(carol), /who="owner"/);
  const before = ov.calls.length;
  assert.doesNotMatch(await recall.forEvent(carol), /who="sender"/);
  assert.equal(ov.calls.length, before, 'nothing is asked for again at once');
  // openviking_remember stored one: the next line shows it.
  for (const uri of [`${ROOT}/preferences/${personDirectory(carol)}/称呼.md`]) {
    tree[uri] = '- 叫她 Carol';
    tree[uri.replace('viking://user/owner.asd/', 'viking://~/')] = '- 叫她 Carol';
  }
  recall.forgetPreferences();
  assert.match(await recall.forEvent(carol), /<memory who="sender" type="preferences">\n## 称呼\n- 叫她 Carol\n<\/memory>/);
});

test('a person is read from their own directory without listing everyone; a renamed one is found by paging through all', async (t) => {
  const filler = Object.fromEntries(Array.from({ length: 2_500 }, (_, i) =>
    [`${ROOT}/preferences/p${i}~${i.toString(16).padStart(12, '0')}/x.md`, '- x']));
  const renamed = chatEvent({ senderId: 'ou_renamed', chatId: 'oc-group', messageId: 'om-r' });
  const steady = chatEvent({ senderId: 'ou_steady', chatId: 'oc-group', messageId: 'om-s' });
  const ov = fakeOV({ files: withAlias({ ...filler,
    [`${ROOT}/preferences/旧名字~${personId(renamed)}/称呼.md`]: '- 叫他老王',
    [`${ROOT}/preferences/${personDirectory(steady)}/语言.md`]: '- 用英文',
  }) });
  const recall = new OVRecall({ configPath: configFile(t), fetchImpl: ov.fetchImpl, logger });
  const everyone = () => ov.calls.map(({ url }) => new URL(url).searchParams)
    .filter((query) => query.get('uri') === 'viking://~/memories/preferences');
  assert.match(await recall.forEvent(steady), /<memory who="sender" type="preferences">\n## 语言\n- 用英文/);
  assert.deepEqual(everyone(), [], 'found under their name: no listing of everyone');
  assert.match(await recall.forEvent(renamed), /<memory who="sender" type="preferences">\n## 称呼\n- 叫他老王/);
  assert.deepEqual(everyone().map((query) => query.get('offset')), ['0', '2000'], 'every page, not the first 1000');
});

test('preferences show newest first and whole within a budget, the owner\'s larger; the rest are named, not read', async (t) => {
  const many = chatEvent({ senderId: 'ou_many', chatId: 'oc-group', messageId: 'om-m' });
  const tree = {};
  const times = {};
  const add = (directory, count, label) => {
    for (let i = 0; i < count; i += 1) {
      const uri = `${ROOT}/preferences/${directory}/${label}${String(i).padStart(2, '0')}.md`;
      tree[uri] = `- ${label} ${i}：${'内容'.repeat(40)}`;
      times[uri] = new Date(Date.UTC(2026, 10, 1, 0, i)).toISOString();
    }
  };
  add('owner', 40, '规则');
  add(personDirectory(many), 20, '偏好');
  const ov = fakeOV({ files: withAlias(tree), times });
  const recall = new OVRecall({ configPath: configFile(t), fetchImpl: ov.fetchImpl, logger });
  const block = await recall.forEvent(many);
  const section = (who) => block.split(`<memory who="${who}" type="preferences">\n`)[1].split('\n</memory>')[0];
  const [ownerShown, ownerNote] = section('owner').split('\n\n(Not shown, older: ');
  const [senderShown, senderNote] = section('sender').split('\n\n(Not shown, older: ');
  assert.ok(ownerShown.startsWith('## 规则39\n') && senderShown.startsWith('## 偏好19\n'), 'newest first');
  assert.ok(ownerShown.length <= 3_000 && ownerShown.length > 2_500, `owner shown ${ownerShown.length}`);
  assert.ok(senderShown.length <= 1_500 && senderShown.length > 1_200, `sender shown ${senderShown.length}`);
  assert.doesNotMatch(ownerShown + senderShown, /…/, 'no topic is cut');
  assert.match(ownerNote, /^规则\d\d(, 规则\d\d)*\. Read them with openviking_read under viking:\/\/~\/memories\/preferences\/owner\/\.\)$/);
  assert.match(ownerNote, /规则00/);
  assert.match(senderNote, /偏好00/);
  const reads = ov.calls.filter(({ url }) => url.includes('/content/read')).length;
  assert.ok(reads < 60, `files past the budget are not read (${reads} of 60)`);
});

test('recall never holds a line up for long or fails it, a broken config included', async (t) => {
  const slow = fakeOV({ entries: entries(), files: withAlias(files()), delayMs: 5_000 });
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
    mentionTarget: (event) => (event.content.includes('@SuperAgent') ? 'me' : null),
  });
  t.after(() => { harness.stop(); store.close(); });
  for (const fields of [
    { messageId: 'om-dm', chatType: 'p2p', senderId: 'ou_owner' },
    { messageId: 'om-mention', chatType: 'group', content: '@SuperAgent 看下' },
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
