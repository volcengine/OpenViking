// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { AgentRuntime } from '../src/agent-runtime.mjs';
import { OVSessionClient, OVSessionContext } from '../src/ov-session-context.mjs';
import { Store } from '../src/store.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

// HTTP-shaped OV with account/user-scoped sessions and synchronous archiving.
// A lost append/commit response can happen after the remote write has landed.
function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ov-lifecycle-'));
  const store = new Store(':memory:', logger);
  store.initializeCognition('codex', 'owner');
  const remote = { sessions: new Map(), commits: [], reads: [], keys: new Map(), warnings: [], warned: [], failMeta: () => false };
  const ok = (result) => Response.json({ status: 'ok', result });
  const fetch = async (address, init) => {
    if (remote.offline) throw new Error('offline');
    const url = new URL(address);
    const { account, user } = remote.keys.get(init.headers['X-API-Key']);
    const namespace = `${url.origin}/${account}/${user}`;
    const body = init.body && JSON.parse(init.body);
    if (url.pathname === '/health') return Response.json({ status: 'ok', auth_mode: 'api_key', account_id: account, user_id: user });
    if (url.pathname === '/api/v1/system/status') return ok({ initialized: true, user });
    const create = (id) => {
      const key = `${namespace}/${id}`;
      if (!remote.sessions.has(key)) remote.sessions.set(key, { session_id: id, namespace,
        uri: `viking://user/${user}/sessions/${id}`, messages: [], archives: [] });
      return remote.sessions.get(key);
    };
    if (url.pathname === '/api/v1/sessions') return ok(create(body.session_id));
    if (url.pathname === '/api/v1/content/read') {
      const uri = url.searchParams.get('uri');
      remote.reads.push(uri);
      const session = [...remote.sessions.values()].find((s) => s.namespace === namespace && `${s.uri}/messages.jsonl` === uri);
      return session ? ok(session.messages.map((m) => JSON.stringify(m)).join('\n')) : Response.json({ status: 'error' }, { status: 403 });
    }
    const match = url.pathname.match(/^\/api\/v1\/sessions\/([^/]+)(?:\/(.+))?$/);
    assert.ok(match, `unexpected route: ${url.pathname}`);
    const [, id, action] = match;
    // OV's append endpoint can create sessions; GET cannot.
    const session = action === 'messages' ? create(id) : remote.sessions.get(`${namespace}/${id}`);
    if (!session) return Response.json({ status: 'error' }, { status: 404 });
    if (!action && remote.failMeta(id)) return Response.json({ status: 'error' }, { status: 500 });
    if (!action) return ok({ ...session, pending_tokens: session.messages.length * 10 });
    if (action === 'context') return ok({ messages: session.messages, stats: { totalArchives: session.archives.length } });
    if (action === 'messages') {
      session.messages.push(body);
      if (remote.appendTimeout) { remote.appendTimeout = false; throw new Error('lost append response'); }
      return ok({});
    }
    assert.equal(action, 'commit');
    // A commit that never answers in time, or one held until the test opens the gate.
    if (remote.slowCommit?.(id)) {
      init.signal.throwIfAborted();
      // A real pending HTTP request holds a socket open. The in-memory mock
      // needs its own referenced handle because AbortSignal.timeout is unrefed.
      await new Promise((_, reject) => {
        const watchdog = setTimeout(() => reject(new Error('mock commit outlived its request timeout')), 2_000);
        init.signal.addEventListener('abort', () => {
          clearTimeout(watchdog);
          reject(init.signal.reason);
        }, { once: true });
      });
    }
    if (remote.commitGate) await remote.commitGate;
    remote.commits.push({ id, body });
    const timeout = remote.commitTimeout;
    remote.commitTimeout = null;
    if (timeout === 'before') throw new Error('commit unavailable');
    if (session.messages.length) {
      session.archives.push(session.messages);
      session.messages = [];
    }
    if (timeout === 'after') throw new Error('lost commit response');
    return ok({});
  };
  const mirrors = [];
  const connect = ({ account = 'team', user = 'owner', key = `${account}-${user}-key`, url = 'https://ov.test', timeoutMs } = {}) => {
    remote.keys.set(key, { account, user });
    const config = path.join(directory, `config-${mirrors.length}.json`);
    // Stale configured headers must not override the key's authenticated identity.
    fs.writeFileSync(config, JSON.stringify({ url, api_key: key, account: 'stale-account', user: 'stale-user' }));
    const client = new OVSessionClient(config, { fetch, ...(timeoutMs ? { timeoutMs } : {}) });
    const mirror = new OVSessionContext(store, client, { ...logger, warn: (event, fields) => {
      remote.warnings.push(event);
      remote.warned.push({ event, ...fields });
    } });
    mirrors.push(mirror);
    return mirror;
  };
  t.after(() => {
    for (const mirror of mirrors) mirror.stop();
    store.close();
    fs.rmSync(directory, { recursive: true, force: true });
  });
  const append = (text, fields = {}) => store.appendCognitionJournal({ kind: 'inbound', actor: 'owner', text, ...fields });
  const row = (epoch = store.getCognitionState().epoch) => store.db.prepare('SELECT * FROM ov_session_sync WHERE epoch=?').get(epoch);
  const session = (id) => [...remote.sessions.values()].find((s) => s.session_id === id);
  return { store, remote, connect, append, row, session };
}

test('/new finalizes a short conversation once, including after daemon restart', async (t) => {
  const { store, remote, connect, append, row, session } = fixture(t);
  const mirror = connect();
  append('remember this preference');
  await mirror.checkpoint(1);
  const old = row();
  assert.equal(remote.commits.length, 0, 'below the normal 24,000-token threshold');
  const runtime = new AgentRuntime({}, store, logger);
  runtime.runtime = 'codex';
  runtime.backend = {
    policyHash: 'policy',
    async prepareSession() { return { prepared: { id: 'fresh', sessionReady: true } }; },
    applyCognition() {},
    commitSession() { return 'fresh'; },
  };
  await runtime._rotateSession({ resume: false });
  assert.equal(store.getCognitionState().epoch, 2);
  mirror.stop();
  const restarted = connect();
  await restarted.checkpoint(2);
  assert.deepEqual(remote.commits, [{ id: old.session_id, body: { keep_recent_count: 0 } }]);
  assert.equal(session(old.session_id).messages.length, 0);
  assert.match(session(old.session_id).archives[0][0].content, /remember this preference/);
  await restarted.checkpoint(2);
  restarted.stop();
  await connect().checkpoint(2);
  assert.equal(remote.commits.length, 1, 'finalization survives restart');
});

test('an epoch retired before its first sync is uploaded and archived in order', async (t) => {
  const { store, connect, append, row, session } = fixture(t);
  const mirror = connect();
  append('first');
  append('second');
  store.bumpCognitionEpoch('codex', 'owner');
  append('current conversation');
  await mirror.checkpoint(2);
  const old = session(row(1).session_id);
  assert.equal(old.archives.length, 1);
  assert.match(old.archives[0][0].content, /first/);
  assert.match(old.archives[0][1].content, /second/);
  assert.equal(session(row(2).session_id).archives.length, 0);
  assert.match(session(row(2).session_id).messages[0].content, /current conversation/);
});

test('first binding does not import unlinked historical epochs of an unknown identity', async (t) => {
  const { store, remote, connect, append, row } = fixture(t);
  append('unlinked history');
  store.bumpCognitionEpoch('codex', 'owner');
  const mirror = connect({ user: 'owner.primary' });
  append('current');
  await mirror.checkpoint(2);
  assert.equal(row(1), undefined);
  assert.equal(remote.sessions.size, 1);
  assert.equal(remote.commits.length, 0);
});

test('binding at startup preserves an unmirrored epoch across rotation and restart', async (t) => {
  const { store, connect, append, row, session } = fixture(t);
  const mirror = connect();
  await mirror.bindIdentity();
  append('not mirrored before restart');
  store.bumpCognitionEpoch('codex', 'owner');
  mirror.stop();
  await connect().checkpoint(2);
  assert.match(session(row(1).session_id).archives[0][0].content, /not mirrored before restart/);
});

test('unsettled old deliveries wait without blocking current sync and finalize after settlement', async (t) => {
  const { store, remote, connect, append, row, session } = fixture(t);
  const mirror = connect();
  append('before');
  const pending = append('in flight', { kind: 'outbound', status: 'PENDING' });
  append('after');
  store.bumpCognitionEpoch('codex', 'owner');
  append('current');
  await mirror.checkpoint(2);
  assert.equal(remote.commits.length, 0);
  assert.equal(session(row(1).session_id).messages.length, 1);
  assert.equal(session(row(2).session_id).messages.length, 1);
  store.db.prepare("UPDATE cognition_journal SET status='UNCERTAIN' WHERE seq=?").run(pending);
  await mirror.checkpoint(2); // Current head unchanged; old work must still retry.
  assert.equal(session(row(1).session_id).archives[0].length, 3);
  assert.equal(remote.commits.length, 1);
});

for (const timing of ['before', 'after']) test(`a final commit that fails ${timing} archiving retries after restart`, async (t) => {
  const { store, remote, connect, append, row, session } = fixture(t);
  const mirror = connect();
  append('archive me');
  store.bumpCognitionEpoch('codex', 'owner');
  append('current');
  remote.commitTimeout = timing;
  await mirror.checkpoint(2);
  const old = session(row(1).session_id);
  assert.equal(old.archives.length, timing === 'after' ? 1 : 0);
  assert.equal(session(row(2).session_id).messages.length, 1);
  assert.ok(remote.warnings.includes('ov_epoch_finalize_failed'));
  mirror.stop();
  const restarted = connect();
  await restarted.checkpoint(2);
  await restarted.checkpoint(2);
  assert.equal(remote.commits.length, 2);
  assert.equal(old.archives.length, 1, 'an empty-session retry does not duplicate the archive');
});

for (const changed of [{ user: 'owner.primary' }, { account: 'other-team' }, { url: 'https://other-ov.test' }]) {
  test(`changing ${Object.keys(changed)[0]} replays the current epoch and abandons the old uncertain cursor`, async (t) => {
    const { store, remote, connect, append, row, session } = fixture(t);
    const first = connect();
    append('old archived history');
    store.bumpCognitionEpoch('codex', 'owner');
    append('earlier current fact');
    await first.checkpoint(2);
    const retired = session(row(1).session_id);
    const previous = row(2);
    append('accepted remotely with no acknowledgement');
    remote.appendTimeout = true;
    await assert.rejects(first.prepare(2));
    assert.ok(row(2).pending_seq);
    first.stop();
    const next = connect(changed);
    assert.equal(store.ovSessionFor(2), null, 'recall and read tools never use the old identity link');
    append('later current fact');
    const context = await next.prepare(2);
    const current = row(2);
    assert.notEqual(current.session_id, previous.session_id);
    assert.notEqual(current.identity, previous.identity);
    assert.equal(current.pending_seq, null);
    assert.equal(context.messages.length, 3);
    assert.match(context.messages[0].content, /earlier current fact/);
    assert.match(context.messages[2].content, /later current fact/);
    assert.equal(store.ovSessionFor(2).session_id, current.session_id);
    assert.equal(store.ovSessionFor(1), null);
    await assert.rejects(next.prepare(1), /another OpenViking connection/);
    await next.checkpoint(2);
    assert.equal(retired.archives.length, 1, 'old archives stay in the original identity');
    assert.equal(session(previous.session_id).messages.length, 2, 'old live session is untouched');
    assert.deepEqual(remote.reads, [], 'never reconcile against the old forbidden URI');
    next.stop();
    await connect(changed).checkpoint(2);
    assert.equal(session(current.session_id).messages.length, 3, 'restart must not replay again');
  });
}

test('rotating a key for the same identity keeps the cursor and reconciles its pending append', async (t) => {
  const { store, remote, connect, append, row, session } = fixture(t);
  const first = connect({ key: 'old-secret' });
  append('only once');
  remote.appendTimeout = true;
  await assert.rejects(first.prepare(1));
  const previous = row();
  first.stop();
  const rotated = connect({ key: 'new-secret' });
  await rotated.checkpoint(1);
  assert.equal(row().identity, previous.identity);
  assert.equal(row().session_id, previous.session_id);
  assert.equal(row().pending_seq, null);
  assert.equal(session(previous.session_id).messages.length, 1);
  assert.deepEqual(remote.reads, [`${previous.uri}/messages.jsonl`]);
  const metadata = store.db.prepare("SELECT value FROM meta WHERE key='ov_session_identity'").get().value;
  assert.doesNotMatch(metadata, /old-secret|new-secret/);
});

for (const same of [true, false]) test(`a legacy cursor is ${same ? 'adopted after namespace verification' : 'replaced when its session is absent in the new identity'}`, async (t) => {
  const { store, remote, connect, append, row, session } = fixture(t);
  const first = connect();
  append('legacy message');
  remote.appendTimeout = true;
  await assert.rejects(first.prepare(1));
  const previous = row();
  first.stop();
  store.db.exec(`DELETE FROM meta WHERE key='ov_session_identity';
    ALTER TABLE ov_session_sync DROP COLUMN identity;
    ALTER TABLE ov_session_sync DROP COLUMN finalized_seq;`);
  const upgraded = connect({ user: same ? 'owner' : 'owner.primary' });
  remote.offline = true;
  assert.deepEqual(await store.prepareOVContext(1), { messages: [] });
  assert.equal(row().pending_seq, previous.pending_seq, 'an outage must preserve recovery intent');
  remote.offline = false;
  await upgraded.checkpoint(1);
  assert.equal(row().session_id === previous.session_id, same);
  assert.equal(row().pending_seq, null);
  assert.ok(row().identity);
  assert.equal(session(row().session_id).messages.length, 1);
});

// A pre-upgrade daemon recorded sync rows with no identity. After the instance
// moves to another OV user, its retired epochs cannot be verified there.
function legacyRows(store) {
  store.db.prepare('UPDATE ov_session_sync SET identity=NULL').run();
  store.db.prepare("DELETE FROM meta WHERE key='ov_session_identity'").run();
}

test('a pre-upgrade retired epoch another connection cannot verify is marked once, not retried every check', async (t) => {
  const { store, remote, connect, append, row, session } = fixture(t);
  const before = connect();
  append('under the old user');
  await before.checkpoint(1);
  before.stop();
  legacyRows(store);
  store.bumpCognitionEpoch('codex', 'owner');
  append('under the new user');
  const after = connect({ user: 'owner.asd' });
  for (let i = 0; i < 3; i++) await after.checkpoint(2);
  assert.deepEqual(remote.warnings, ['ov_epochs_left_behind'], 'said once, no failure on every check');
  assert.deepEqual(remote.warned[0].epochs, [1]);
  assert.equal(row(1).identity, 'foreign');
  assert.equal(remote.commits.length, 0, 'nothing committed into either user');
  assert.equal(session(row(1).session_id).archives.length, 0, 'the old session is left as it was');
  assert.match(session(row(2).session_id).messages[0].content, /under the new user/);
  await assert.rejects(after.prepare(1), /another OpenViking connection/);
  assert.equal(store.ovSessionFor(1), null);
});

test('a pre-upgrade retired epoch under the same connection is adopted and finalized', async (t) => {
  const { store, remote, connect, append, row, session } = fixture(t);
  const before = connect();
  append('short conversation');
  await before.checkpoint(1);
  before.stop();
  legacyRows(store);
  store.bumpCognitionEpoch('codex', 'owner');
  append('current');
  await connect().checkpoint(2);
  assert.ok(row(1).identity && row(1).identity !== 'foreign');
  assert.equal(session(row(1).session_id).archives.length, 1);
  assert.deepEqual(remote.warnings, []);
});

test('a failing current epoch does not hold up retired ones, and is still reported', async (t) => {
  const { store, remote, connect, append, row, session } = fixture(t);
  const mirror = connect();
  append('retired conversation');
  await mirror.checkpoint(1);
  store.bumpCognitionEpoch('codex', 'owner');
  append('current conversation');
  remote.failMeta = (id) => id === row(2)?.session_id;
  await assert.rejects(mirror.checkpoint(2), /HTTP 500/);
  assert.equal(session(row(1).session_id).archives.length, 1, 'finalized despite the current failure');
  assert.equal(row(1).finalized_seq, store.cognitionJournalHead(1));
});

test('an unreachable OV leaves retired epochs for the next check, and a failure says why', async (t) => {
  const { store, remote, connect, append, row } = fixture(t);
  const mirror = connect();
  append('retired conversation');
  await mirror.checkpoint(1);
  store.bumpCognitionEpoch('codex', 'owner');
  append('current conversation');
  remote.offline = true;
  await assert.rejects(mirror.checkpoint(2));
  assert.deepEqual(remote.warnings, [], 'no per-epoch warning while OV is down');
  assert.equal(row(1).finalized_seq, -1);
  remote.offline = false;
  remote.commitTimeout = 'before';
  await mirror.checkpoint(2);
  const failed = remote.warned.find(({ event }) => event === 'ov_epoch_finalize_failed');
  assert.equal(failed.epoch, 1);
  assert.match(failed.error, /OpenViking request failed/);
  await mirror.checkpoint(2);
  assert.equal(row(1).finalized_seq, store.cognitionJournalHead(1), 'retried on the next check');
});

test('one retired session timing out does not stop the next from being finalized', async (t) => {
  const { store, remote, connect, append, row, session } = fixture(t);
  const mirror = connect({ timeoutMs: 50 });
  append('first retired');
  store.bumpCognitionEpoch('codex', 'owner');
  append('second retired');
  store.bumpCognitionEpoch('codex', 'owner');
  append('current');
  // The first retired session's commit never answers in time; it comes first.
  remote.slowCommit = (id) => id === row(1)?.session_id;
  await mirror.checkpoint(3);
  assert.equal(session(row(2).session_id).archives.length, 1, 'the next retired epoch got its turn');
  assert.equal(row(1).finalized_seq, -1);
  const timedOut = remote.warned.find(({ event, epoch }) => event === 'ov_epoch_finalize_failed' && epoch === 1);
  assert.match(timedOut.error, /timed out/);
  remote.slowCommit = null;
  await mirror.checkpoint(3);
  assert.equal(session(row(1).session_id).archives.length, 1, 'retried on the next check');
});

test('finalizing retired epochs does not hold up the history a fresh session needs after /new', async (t) => {
  const { store, remote, connect, append, row, session } = fixture(t);
  const mirror = connect();
  append('retired conversation');
  await mirror.checkpoint(1);
  store.bumpCognitionEpoch('codex', 'owner');
  append('first message after /new');
  // OV holds the full commit, as it does until the archive is written.
  let open;
  remote.commitGate = new Promise((go) => { open = go; });
  const checkpoint = mirror.checkpoint(2);
  let timer;
  const context = await Promise.race([mirror.prepare(2), new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('history waited for the retired epoch')), 2_000);
  })]).finally(() => clearTimeout(timer));
  assert.match(context.messages.at(-1).content, /first message after \/new/, 'history is ready while the commit waits');
  assert.equal(session(row(1).session_id).archives.length, 0, 'the retired epoch is still being finalized');
  open();
  await checkpoint;
  assert.equal(session(row(1).session_id).archives.length, 1);
});
