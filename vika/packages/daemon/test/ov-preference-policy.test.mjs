// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { ActionService } from '../src/actions/service.mjs';
import { formatOVJournalLine } from '../src/cognition.mjs';
import { personKey } from '../src/ov-person.mjs';
import { OVSessionClient, OVSessionContext } from '../src/ov-session-context.mjs';
import { OVToolClient } from '../src/ov-tools.mjs';
import { Store } from '../src/store.mjs';
import { fakeConnectors } from './action-fixture.mjs';
import { chatEvent, subjectEvent } from './event-fixture.mjs';
import { fakeOV } from './ov-server-fixture.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {} };

// These are real client/service requests to a scripted HTTP server. They test
// policy routing and source attribution, not model extraction quality.
test('threshold and retired-history commits retain the history policy after explicit preference capture', async (t) => {
  const ov = await fakeOV(t);
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ov-preference-policy-'));
  const configPath = path.join(directory, 'ov.json');
  fs.writeFileSync(configPath, JSON.stringify({ url: ov.url, user: 'test-user' }));
  const store = new Store(':memory:', logger);
  const { epoch } = store.initializeCognition('codex', 'runtime');
  const history = new OVSessionContext(store, new OVSessionClient(configPath), logger);
  t.after(() => { history.stop(); store.close(); fs.rmSync(directory, { recursive: true, force: true }); });
  const service = new ActionService({ config: {}, store, logger,
    connectors: fakeConnectors({ ids: ['lark', 'lark-second'] }), ovTools: new OVToolClient({ configPath }) });
  const events = [chatEvent({ senderId: 'writer-alpha', messageId: 'om_alpha', content: 'Please always answer me in French.' }),
    chatEvent({ senderId: 'writer-beta', messageId: 'om_beta', content: 'Please always answer me in German.' })];
  const rows = events.map((event) => store.ingest(event).inbound);
  const prepared = await history.prepare(epoch);
  const first = ov.state.sessions.get(prepared.sessionId);
  const policy = { memory_types: ['profile', 'entities', 'events'] };
  assert.deepEqual(first.memoryPolicy, policy);
  first.pendingTokens = 24_000;
  await history.checkpoint(epoch);
  assert.equal(first.commits, 1, 'threshold commit went through the real HTTP client');

  const remembered = [];
  for (const [index, event] of events.entries()) {
    const result = await service.call('openviking_remember', { decision_id: event.eventId,
      note: 'Preserve the language preference stated by this sender.' }, { authority: 'runtime' });
    const record = ov.state.sessions.get(result.data.session_id);
    assert.deepEqual(record.memoryPolicy, { memory_types: ['preferences'] });
    assert.equal(record.commits, 1);
    assert.deepEqual(record.messages, [
      { role: 'user', content: formatOVJournalLine(store.journalItemForInbound(rows[index].seq)) },
      { role: 'assistant', content: '[from=me] Preserve the language preference stated by this sender.' },
    ]);
    assert.ok(record.messages[0].content.includes(`from=${personKey(event)}`));
    assert.ok(!record.messages[0].content.includes(personKey(events[1 - index])));
    remembered.push(result.data.session_id);
  }
  assert.notEqual(remembered[0], remembered[1]);
  assert.equal(store.db.prepare('SELECT count(*) AS n FROM decisions').get().n, 0);
  assert.equal(store.db.prepare('SELECT count(*) AS n FROM outbox').get().n, 0);

  const next = store.bumpCognitionEpoch('codex', 'runtime');
  await history.checkpoint(next.epoch);
  assert.equal(first.commits, 2, 'retired history also commits after preference capture');
  for (const request of ov.state.requests.filter(({ route }) => route.endsWith('/commit'))) {
    assert.equal(request.body.memory_policy, undefined, 'commits inherit their own session policy');
    const id = request.route.split('/').at(-2);
    const actual = ov.state.sessions.get(id).memoryPolicy;
    assert.deepEqual(actual, remembered.includes(id) ? { memory_types: ['preferences'] } : policy);
  }
  const sessions = ov.state.requests.filter(({ route }) => route === 'POST /api/v1/sessions');
  assert.equal(sessions.length, 4, 'two history sessions and two preference sessions');
  assert.deepEqual(first.memoryPolicy, policy, 'remember cannot broaden the history policy');
});

test('an assistant note cannot originate a preference for a senderless event', async (t) => {
  const store = new Store(':memory:', logger);
  store.initializeCognition('codex', 'runtime');
  t.after(() => store.close());
  const event = subjectEvent();
  store.ingest(event);
  let called = false;
  const service = new ActionService({ config: {}, store, logger, connectors: fakeConnectors(),
    ovTools: { remember() { called = true; } } });
  await assert.rejects(service.call('openviking_remember', { decision_id: event.eventId,
    note: 'The owner prefers French.' }, { authority: 'runtime' }), { code: 'not_rememberable' });
  assert.equal(called, false);
});
