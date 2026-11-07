// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import { test } from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';
import { ActionService } from '../src/actions/service.mjs';
import { personKey } from '../src/ov-person.mjs';
import { OVSessionClient, OVSessionContext } from '../src/ov-session-context.mjs';
import { OVToolClient } from '../src/ov-tools.mjs';
import { Store } from '../src/store.mjs';
import { fakeConnectors } from './action-fixture.mjs';
import { chatEvent } from './event-fixture.mjs';

// Opt in only against a NEW disposable localhost OV configured with a real
// extraction model. This writes synthetic data and leaves it for inspection.
// Never point it at a production server or reuse an account with real memory.
const configPath = process.env.VIKA_OV_PREFERENCE_TEST_CONFIG;
const logger = { debug() {}, info() {}, warn() {}, error() {} };

test('live OV: history does not extract preferences; explicit capture attributes each preference to its source sender', {
  skip: !configPath && 'set VIKA_OV_PREFERENCE_TEST_CONFIG for a disposable real-model OV', timeout: 600_000,
}, async (t) => {
  const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  const url = new URL(config.url);
  assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname), 'test requires a disposable localhost server');
  assert.ok(String(config.account).startsWith('vika-pref-test'), 'test requires a dedicated account');
  assert.ok(String(config.user).startsWith('vika-pref-test-'), 'test requires a dedicated user');
  const wire = [];
  const fetchImpl = async (url, options) => {
    const body = options?.body && typeof options.body === 'string' ? JSON.parse(options.body) : null;
    const response = await fetch(url, options);
    if (body && new URL(url).pathname.endsWith('/sessions')) wire.push(body.memory_policy);
    return response;
  };
  const client = new OVSessionClient(configPath, { fetch: fetchImpl, timeoutMs: 120_000 });
  const tools = new OVToolClient({ configPath, fetch: fetchImpl, timeoutMs: 120_000 });
  const files = async () => {
    let entries;
    try { entries = await client.request('/fs/ls?uri=viking%3A%2F%2F~%2Fmemories&recursive=true&node_limit=1000&output=original'); }
    catch (error) { if (error.httpStatus === 404) return new Map(); throw error; }
    const contents = new Map();
    for (const entry of entries ?? []) {
      if (!entry.isDir && entry.uri.endsWith('.md') && !entry.uri.split('/').at(-1).startsWith('.')) {
        contents.set(entry.uri, await client.read(entry.uri, 0, -1));
      }
    }
    return contents;
  };
  const waitTask = async (id) => {
    assert.ok(id, 'an extraction task must actually be submitted');
    const deadline = Date.now() + 180_000;
    while (Date.now() < deadline) {
      const task = await client.request(`/tasks/${encodeURIComponent(id)}`);
      if (task.status === 'completed') return;
      assert.ok(!['failed', 'cancelled'].includes(task.status), 'extraction failed; inspect the isolated server log');
      await delay(500);
    }
    assert.fail('extraction did not complete within 180 seconds');
  };
  const preferences = (snapshot) => new Map([...snapshot].filter(([uri]) => uri.includes('/memories/preferences/')));
  const store = new Store(':memory:', logger);
  const { epoch } = store.initializeCognition('codex', 'test-runtime');
  const mirror = new OVSessionContext(store, client, logger);
  t.after(() => { mirror.stop(); store.close(); });
  const service = new ActionService({ config: {}, store, logger,
    connectors: fakeConnectors({ ids: ['lark'], owners: {} }), ovTools: tools });
  const alpha = chatEvent({ senderId: 'writer-alpha', messageId: 'om_alpha', content: 'Please remember my lasting language preference: always answer me in French.' });
  const beta = chatEvent({ senderId: 'writer-beta', messageId: 'om_beta', content: 'Please remember my lasting language preference: always answer me in German.' });
  for (const event of [alpha, beta, chatEvent({ senderId: 'writer-alpha', messageId: 'om_fact',
    content: 'The Lantern prototype shipped today. I lead its engineering team. This is a completed milestone, not a preference.' })]) store.ingest(event);
  assert.equal((await files()).size, 0, 'requires a fresh memory namespace');
  const history = await mirror.prepare(epoch);
  await waitTask((await client.commit(history.sessionId, { final: true })).task_id);
  const automatic = await files();
  assert.equal(preferences(automatic).size, 0, 'ordinary history must not create preferences');
  assert.ok([...automatic.keys()].some((uri) => /\/memories\/(?:events|entities)\//.test(uri)),
    'positive control: automatic extraction must produce an event/entity, not just an empty result');

  for (const [event, language] of [[alpha, /French|法语/i], [beta, /German|德语/i]]) {
    const before = await files();
    const result = await service.call('openviking_remember', { decision_id: event.eventId,
      note: 'This is the sender\'s own stated lasting language preference.' }, { authority: 'test-runtime' });
    await waitTask(result.data.task_id);
    const after = await files();
    const changed = [...after].filter(([uri, content]) => before.get(uri) !== content);
    assert.ok(changed.length > 0, 'positive control: remember must actually write a preference');
    for (const [uri, content] of changed) {
      assert.ok(uri.includes(`/memories/preferences/${personKey(event)}/`), 'every changed preference must belong to the original sender');
      assert.match(content, language);
    }
    for (const [uri, content] of before) assert.equal(after.get(uri), content, 'capture must leave existing memories unchanged');
  }
  // A later ordinary commit must neither add preferences nor rewrite those
  // already captured, even when its raw history contains a conflicting request.
  const beforeLaterHistory = preferences(await files());
  store.ingest(chatEvent({ senderId: 'writer-alpha', messageId: 'om_later',
    content: 'Please always answer me in Italian instead. The Lantern team also finished the launch review today.' }));
  await mirror.prepare(epoch);
  await waitTask((await client.commit(history.sessionId, { final: true })).task_id);
  assert.deepEqual(preferences(await files()), beforeLaterHistory);
  assert.deepEqual(wire, [
    { memory_types: ['profile', 'entities', 'events'] },
    { memory_types: ['preferences'] }, { memory_types: ['preferences'] },
  ]);
  t.diagnostic('Real-model positive writes checked by filesystem reads; no find/search acceptance claim.');
});
