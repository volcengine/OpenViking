// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { Store } from '../src/store.mjs';
import { OVSessionClient, OVSessionContext } from '../src/ov-session-context.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {} };
const ok = (result, status = 200) => Response.json({ status: 'ok', result }, { status });

// Both protocols use the same HTTP client and durable store. Accepted jobs are
// independent of request lifetime: tests complete them after timeout/restart.
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ov-session-tasks-'));
  const database = path.join(dir, 'state.sqlite');
  const sessions = new Map();
  const jobs = new Map();
  const posts = [];
  const polls = [];
  const clients = [];
  const remote = { async: true, held: new Set(), fail: null, archived: false, malformed: false };
  const namespaceKey = (namespace, id) => `${namespace}/${id}`;
  const getSession = (namespace, id) => sessions.get(namespaceKey(namespace, id));
  const newSession = (namespace, id) => {
    const session = { session_id: id, uri: `viking://user/${namespace}/sessions/${id}`, messages: [], pending_tokens: 0, archives: 0 };
    sessions.set(namespaceKey(namespace, id), session);
    return session;
  };
  const finish = (job) => {
    if (job.status !== 'consuming') return;
    if (remote.fail === job.operation) {
      job.status = 'failed';
      job.error = { code: 'WRITE_REJECTED', message: 'private message secret-api-key' };
      return;
    }
    let session = getSession(job.namespace, job.id);
    if (job.operation === 'create') {
      if (session) {
        job.status = 'failed'; job.error = `Resource already exists: session ${job.id} already exists`;
        return;
      }
      session = newSession(job.namespace, job.id);
      job.result = session;
    } else if (job.operation === 'append') {
      session.messages.push(job.body); session.pending_tokens += 25_000;
      job.result = {};
    } else {
      session.archives++; session.messages = []; session.pending_tokens = 0;
      job.result = { archived: true };
    }
    job.status = 'completed';
  };
  const connect = (namespace = 'owner') => {
    const config = path.join(dir, `${namespace}.json`);
    fs.writeFileSync(config, JSON.stringify({ url: 'https://ov.test', api_key: namespace }));
    const client = new OVSessionClient(config, { taskPollMs: 1, taskTimeoutMs: 40, fetch: async (address, init) => {
      const url = new URL(address);
      const body = init.body && JSON.parse(init.body);
      if (url.pathname.startsWith('/api/v1/tasks/')) {
        const id = decodeURIComponent(url.pathname.slice('/api/v1/tasks/'.length));
        polls.push({ namespace, id });
        const job = jobs.get(id);
        if (!job || job.namespace !== namespace) return Response.json({ status: 'error' }, { status: 404 });
        if (!remote.held.has(job.operation)) finish(job);
        return ok({ task_id: id, task_type: { create: 'create_session', append: 'add_message', commit: 'session_commit' }[job.operation],
          resource_id: job.id, status: job.status, result: job.result, error: job.error });
      }
      if (url.pathname === '/api/v1/content/read') {
        const uri = url.searchParams.get('uri');
        const session = [...sessions.values()].find((s) => `${s.uri}/messages.jsonl` === uri);
        return ok((session?.messages ?? []).map((m) => JSON.stringify(m)).join('\n'));
      }
      const match = url.pathname.match(/^\/api\/v1\/sessions(?:\/([^/]+)(?:\/(.+))?)?$/);
      assert.ok(match, url.pathname);
      const id = match[1] ? decodeURIComponent(match[1]) : body.session_id;
      const action = match[2];
      const operation = !match[1] ? 'create' : action === 'messages' ? 'append' : 'commit';
      let session = getSession(namespace, id);
      if (init.method === 'GET') {
        if (!session) return Response.json({ status: 'error' }, { status: 404 });
        return ok(action === 'context' ? { messages: session.messages } : session);
      }
      posts.push({ namespace, operation, id, body });
      const taskId = `task-${jobs.size + 1}`;
      const job = { namespace, operation, id, body, status: 'consuming' };
      jobs.set(taskId, job);
      if (!remote.async) { finish(job); return ok(job.result); }
      // Model a concurrent creator winning between GET and POST.
      if (remote.race && operation === 'create') { newSession(namespace, id); remote.race = false; }
      if (remote.archived && operation === 'commit') {
        finish(job); job.status = 'consuming'; // extraction is still running
      }
      return ok({ accepted: true, task_id: remote.malformed ? undefined : taskId,
        session_id: id, uri: `viking://user/${namespace}/sessions/${id}` }, 202);
    } });
    client.namespace = namespace;
    const store = new Store(database, logger);
    store.initializeCognition('codex', 'owner');
    const service = new OVSessionContext(store, client, logger);
    const entry = { client, store, service, close() { service.stop(); store.close(); this.closed = true; } };
    clients.push(entry);
    return entry;
  };
  t.after(() => { for (const c of clients) if (!c.closed) c.close(); fs.rmSync(dir, { recursive: true, force: true }); });
  const append = (store, text = 'hello') => store.appendCognitionJournal({ kind: 'inbound', actor: 'owner', text });
  const state = (store, epoch = store.getCognitionState().epoch) => store.db.prepare('SELECT * FROM ov_session_sync WHERE epoch=?').get(epoch);
  return { connect, append, state, remote, posts, polls, jobs, sessions, newSession };
}

test('synchronous and asynchronous servers share a completed create/append/commit contract', async (t) => {
  for (const async of [false, true]) await t.test(String(async), async (t) => {
    const f = fixture(t); f.remote.async = async;
    const { store, service } = f.connect();
    const { epoch } = store.getCognitionState();
    f.append(store);
    await service.checkpoint(epoch);
    const row = f.state(store);
    assert.ok(row.uri);
    assert.equal(row.synced_seq, store.cognitionJournalHead(epoch));
    assert.equal(row.pending_task, null);
    assert.deepEqual(f.posts.map((p) => p.operation), ['create', 'append', 'commit']);
    assert.equal(f.polls.length, async ? 3 : 0);
  });
});

for (const operation of ['create', 'append', 'commit']) {
  test(`accepted ${operation} survives timeout/restart without resubmission or early cursor advancement`, async (t) => {
    const f = fixture(t); f.remote.held.add(operation);
    let current = f.connect();
    const { epoch } = current.store.getCognitionState();
    f.append(current.store);
    await assert.rejects(current.service.checkpoint(epoch), /RESULT_UNKNOWN/);
    const before = f.state(current.store);
    const pending = JSON.parse(before.pending_task);
    assert.equal(pending.operation, operation);
    assert.equal(f.posts.filter((p) => p.operation === operation).length, 1);
    assert.equal(before.finalized_seq, -1);
    if (operation !== 'commit') assert.equal(before.synced_seq, 0);
    // Another checkpoint must not infer success from a pre-generated URI or
    // from absence of a queued append's source ID in the live message file.
    await assert.rejects(current.service.prepare(epoch), /RESULT_UNKNOWN/);
    assert.equal(f.posts.filter((p) => p.operation === operation).length, 1);
    current.close();
    f.remote.held.delete(operation);
    current = f.connect();
    await current.service.checkpoint(epoch);
    assert.equal(f.posts.filter((p) => p.operation === operation).length, 1);
    assert.ok(f.polls.filter((p) => p.id === pending.acceptance.task_id).length >= 2);
    assert.equal(f.state(current.store).pending_task, null);
    assert.equal(f.state(current.store).synced_seq, current.store.cognitionJournalHead(epoch));
  });
}

test('background failure is reported safely and never advances the append cursor', async (t) => {
  const f = fixture(t); f.remote.fail = 'append';
  const { store, service } = f.connect(); f.append(store);
  await assert.rejects(service.prepare(store.getCognitionState().epoch), (error) => {
    assert.equal(error.code, 'WRITE_REJECTED');
    assert.doesNotMatch(error.message, /private|secret/);
    return true;
  });
  assert.equal(f.state(store).synced_seq, 0);
  assert.equal(f.state(store).pending_task, null);
  f.remote.fail = null;
  await service.prepare(store.getCognitionState().epoch);
  assert.equal([...f.sessions.values()][0].messages.length, 1);
});

test('missing or malformed Task stays unresolved; neither cursor nor POST is retried', async (t) => {
  for (const malformed of [false, true]) await t.test(String(malformed), async (t) => {
    const f = fixture(t); f.remote.held.add('append');
    const { store, service } = f.connect();
    await service.session(store.getCognitionState().epoch);
    f.remote.malformed = malformed; f.append(store);
    await assert.rejects(service.prepare(store.getCognitionState().epoch), /RESULT_UNKNOWN|PROTOCOL_ERROR/);
    f.jobs.clear();
    await assert.rejects(service.prepare(store.getCognitionState().epoch), /TASK_NOT_FOUND|PROTOCOL_ERROR/);
    assert.equal(f.posts.filter((p) => p.operation === 'append').length, 1);
    assert.equal(f.state(store).synced_seq, 0);
    assert.ok(f.state(store).pending_task);
  });
});

test('an existing session and ALREADY_EXISTS race recover through authenticated GET', async (t) => {
  for (const race of [false, true]) await t.test(String(race), async (t) => {
    const f = fixture(t); const { store, service } = f.connect();
    const { epoch } = store.getCognitionState();
    if (race) f.remote.race = true;
    else {
      store.db.prepare('INSERT INTO ov_session_sync(epoch,session_id,identity) VALUES (?,?,?)').run(epoch, 'existing', 'owner');
      f.newSession('owner', 'existing');
    }
    const row = await service.session(epoch);
    assert.equal(row.uri, `viking://user/owner/sessions/${row.session_id}`);
    assert.equal(f.posts.filter((p) => p.operation === 'create').length, race ? 1 : 0);
    assert.equal(f.state(store).pending_task, null);
  });
});

test('a legacy fixed session without URI is adopted only after remote identity verification', async (t) => {
  const f = fixture(t); const { store, service } = f.connect();
  const { epoch } = store.getCognitionState();
  store.db.prepare('INSERT INTO ov_session_sync(epoch,session_id) VALUES (?,?)').run(epoch, 'legacy');
  f.newSession('owner', 'legacy');
  await service.session(epoch);
  assert.equal(f.state(store).session_id, 'legacy');
  assert.equal(f.state(store).identity, 'owner');
  assert.equal(f.posts.length, 0);
});

test('retired epoch finalization waits for its original Task and advances once after restart', async (t) => {
  const f = fixture(t); let current = f.connect();
  const { epoch } = current.store.getCognitionState(); f.append(current.store);
  await current.service.prepare(epoch);
  f.remote.held.add('commit');
  current.store.bumpCognitionEpoch('codex', 'owner');
  await current.service.checkpoint(current.store.getCognitionState().epoch);
  const before = f.state(current.store, epoch);
  assert.equal(before.finalized_seq, -1);
  assert.equal(JSON.parse(before.pending_task).operation, 'finalize');
  current.close(); f.remote.held.delete('commit'); current = f.connect();
  await current.service.checkpoint(current.store.getCognitionState().epoch);
  const after = f.state(current.store, epoch);
  assert.equal(after.finalized_seq, after.synced_seq);
  assert.equal(after.pending_task, null);
  assert.equal(f.posts.filter((p) => p.operation === 'commit' && p.id === before.session_id).length, 1);
});

test('archived Core result completes finalization without waiting for memory extraction', async (t) => {
  const f = fixture(t); const { store, service } = f.connect();
  const { epoch } = store.getCognitionState(); f.append(store);
  await service.prepare(epoch);
  f.remote.archived = true; f.remote.held.add('commit');
  store.bumpCognitionEpoch('codex', 'owner');
  await service.checkpoint(store.getCognitionState().epoch);
  assert.equal(f.state(store, epoch).finalized_seq, f.state(store, epoch).synced_seq);
  assert.equal([...f.jobs.values()].find((job) => job.operation === 'commit').status, 'consuming');
});

test('changing identity never resumes another identity\'s accepted Task', async (t) => {
  const f = fixture(t); f.remote.held.add('append'); let current = f.connect();
  const { epoch } = current.store.getCognitionState(); f.append(current.store);
  await assert.rejects(current.service.prepare(epoch), /RESULT_UNKNOWN/);
  const old = JSON.parse(f.state(current.store).pending_task).acceptance.task_id;
  current.close(); f.remote.held.delete('append'); current = f.connect('other');
  await current.service.prepare(epoch);
  assert.equal(f.polls.filter((p) => p.namespace === 'other' && p.id === old).length, 0);
  assert.equal(f.state(current.store).identity, 'other');
  assert.equal(f.state(current.store).pending_task, null);
});

test('Task/session mismatch is a protocol error, never a successful write', async (t) => {
  const f = fixture(t); f.remote.held.add('create'); const { store, service, client } = f.connect();
  const { epoch } = store.getCognitionState();
  await assert.rejects(service.session(epoch), /RESULT_UNKNOWN/);
  const job = [...f.jobs.values()][0]; job.id = 'another-session';
  await assert.rejects(service.session(epoch), /PROTOCOL_ERROR/);
  assert.equal(f.state(store).uri, null);
  assert.equal(f.posts.length, 1);
  client.fetch = async () => ok({ session_id: 'wrong', uri: 'viking://user/owner/sessions/wrong' });
  await assert.rejects(client.create('expected'), /invalid session identity/);
});

test('HTTP 202 without acceptance fields is never a successful append', async (t) => {
  const f = fixture(t); const { store, service, client } = f.connect();
  const { epoch } = store.getCognitionState();
  await service.session(epoch);
  const fetch = client.fetch; let writes = 0;
  client.fetch = async (url, init) => {
    if (init.method === 'POST' && url.endsWith('/messages')) { writes++; return ok({}, 202); }
    return fetch(url, init);
  };
  f.append(store);
  await assert.rejects(service.prepare(epoch), /PROTOCOL_ERROR/);
  await assert.rejects(service.prepare(epoch), /PROTOCOL_ERROR/);
  assert.equal(writes, 1);
  assert.equal(f.state(store).synced_seq, 0);
});

test('a failed finalization Task cannot mark an epoch archived', async (t) => {
  const f = fixture(t); const { store, service } = f.connect();
  const { epoch } = store.getCognitionState(); f.append(store);
  await service.prepare(epoch); f.remote.fail = 'commit';
  store.bumpCognitionEpoch('codex', 'owner');
  await service.checkpoint(store.getCognitionState().epoch);
  assert.equal(f.state(store, epoch).finalized_seq, -1);
  assert.equal(f.state(store, epoch).pending_task, null);
  f.remote.fail = null;
  await service.checkpoint(store.getCognitionState().epoch);
  assert.equal(f.state(store, epoch).finalized_seq, f.state(store, epoch).synced_seq);
});

test('pending commit blocks a later append until the original Task completes', async (t) => {
  const f = fixture(t); const { store, service } = f.connect();
  const { epoch } = store.getCognitionState(); f.append(store);
  f.remote.held.add('commit');
  await assert.rejects(service.checkpoint(epoch), /RESULT_UNKNOWN/);
  f.append(store, 'later');
  await assert.rejects(service.prepare(epoch), /RESULT_UNKNOWN/);
  assert.equal(f.posts.filter((p) => p.operation === 'append').length, 1);
  f.remote.held.delete('commit');
  await service.prepare(epoch);
  assert.equal(f.posts.filter((p) => p.operation === 'commit').length, 1);
  assert.equal(f.posts.filter((p) => p.operation === 'append').length, 2);
});

test('stopping during accepted Task polling preserves the receipt for recovery', async (t) => {
  const f = fixture(t); const { store, service, client } = f.connect();
  const { epoch } = store.getCognitionState(); f.remote.held.add('create');
  const fetch = client.fetch;
  client.fetch = async (url, init) => {
    const response = await fetch(url, init);
    if (url.includes('/tasks/')) service.stop();
    return response;
  };
  await assert.rejects(service.session(epoch), /stopped/);
  assert.ok(f.state(store).pending_task);
  assert.equal(f.state(store).uri, null);
  assert.equal(f.posts.length, 1);
});

test('expired create Task recovers by exact authenticated session lookup', async (t) => {
  const f = fixture(t); const { store, service } = f.connect();
  const { epoch } = store.getCognitionState(); f.remote.held.add('create');
  await assert.rejects(service.session(epoch), /RESULT_UNKNOWN/);
  const before = f.state(store); f.newSession('owner', before.session_id); f.jobs.clear();
  await service.session(epoch);
  assert.equal(f.state(store).uri, `viking://user/owner/sessions/${before.session_id}`);
  assert.equal(f.state(store).pending_task, null);
  assert.equal(f.posts.length, 1);
});

test('delayed completion is polled with pinned auth and persisted before the first query', async (t) => {
  const f = fixture(t); f.remote.held.add('append');
  const { store, service, client } = f.connect(); const { epoch } = store.getCognitionState();
  await service.session(epoch); f.append(store);
  const fetch = client.fetch; let queries = 0;
  client.fetch = async (url, init) => {
    if (url.includes('/tasks/')) {
      queries++;
      assert.equal(init.headers['X-API-Key'], 'owner');
      assert.ok(JSON.parse(f.state(store).pending_task).acceptance.task_id);
      assert.equal(f.state(store).synced_seq, 0);
      if (queries === 3) f.remote.held.delete('append');
    }
    return fetch(url, init);
  };
  await service.prepare(epoch);
  assert.equal(queries, 3);
  assert.equal(f.posts.filter((p) => p.operation === 'append').length, 1);
  assert.equal(f.state(store).synced_seq, store.cognitionJournalHead(epoch));
});

test('Task query failure retains its receipt without resubmitting the append', async (t) => {
  const f = fixture(t); const { store, service, client } = f.connect();
  const { epoch } = store.getCognitionState(); await service.session(epoch); f.append(store);
  const fetch = client.fetch; let fail = true;
  client.fetch = async (url, init) => {
    if (url.includes('/tasks/') && fail) return Response.json({ status: 'error' }, { status: 503 });
    return fetch(url, init);
  };
  await assert.rejects(service.prepare(epoch), /HTTP 503/);
  assert.ok(f.state(store).pending_task);
  assert.equal(f.state(store).synced_seq, 0);
  fail = false; await service.prepare(epoch);
  assert.equal(f.posts.filter((p) => p.operation === 'append').length, 1);
});
