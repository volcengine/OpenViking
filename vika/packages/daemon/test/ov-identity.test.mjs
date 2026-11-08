// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { loadOVPersonality, PERSONALITIES } from '../src/ov-identity.mjs';
import { assembleConversationPolicy, DEFAULT_PROMPT_DIR, resolveRolePromptPath } from '../src/conversation-policy.mjs';

function memoryClient(initial = {}) {
  const files = new Map(Object.entries(initial));
  const writes = [];
  return {
    files, writes,
    async read(uri) {
      if (!files.has(uri)) throw Object.assign(new Error('missing'), { httpStatus: 404 });
      return files.get(uri);
    },
    async request(route, body) {
      assert.equal(route, '/content/write');
      assert.equal(body.mode, 'create');
      assert.equal(body.wait, false);
      files.set(body.uri, body.content);
      writes.push(body);
    },
  };
}

for (const personality of PERSONALITIES) {
  test(`${personality} is stored in OV and injected for every role`, async () => {
    const client = memoryClient();
    const config = { personality, agentName: '小维' };
    const personalityPrompt = await loadOVPersonality(client, config);
    const seed = fs.readFileSync(path.join(DEFAULT_PROMPT_DIR, 'personalities', `${personality}.md`), 'utf8');
    assert.equal(client.writes[0].uri, `viking://~/memories/identity/personalities/${personality}.md`);
    assert.equal(client.writes[0].content, seed);
    assert.equal(personalityPrompt, seed.replaceAll('{{AGENT_NAME}}', '小维').trim());
    for (const role of ['colleague', 'assistant', 'qa']) {
      const prompt = assembleConversationPolicy({ ...config, personalityPrompt, rolePromptPath: resolveRolePromptPath(role) });
      assert.ok(prompt.includes(personalityPrompt));
      assert.equal(prompt.includes(personality === 'outgoing' ? '### 沉静内敛' : '### 外向活泼'), false);
      assert.ok(prompt.indexOf('## Personality') > prompt.indexOf('## Role'));
    }
    assert.equal(await loadOVPersonality(client, config), personalityPrompt);
    assert.equal(client.writes.length, 1, 'restart preserves the OV file');
  });
}

test('default is outgoing, and switching preserves edits to either preset', async () => {
  const client = memoryClient();
  assert.match(await loadOVPersonality(client), /### 外向活泼/);
  const uri = 'viking://~/memories/identity/personalities/outgoing.md';
  client.files.set(uri, 'My edited personality for {{AGENT_NAME}}.');
  assert.match(await loadOVPersonality(client, { personality: 'reserved' }), /### 沉静内敛/);
  assert.equal(await loadOVPersonality(client, { agentName: '小维' }), 'My edited personality for 小维.');
  assert.equal(client.writes.length, 2);
});

test('OV failures and invalid contents never silently change personality', async () => {
  for (const httpStatus of [401, 403, 500, undefined]) {
    const error = Object.assign(new Error('unavailable'), { httpStatus });
    const client = { read: async () => { throw error; }, request: () => assert.fail('must not seed on an unavailable read') };
    await assert.rejects(loadOVPersonality(client), (actual) => actual === error);
  }
  for (const text of ['', {}, 'x'.repeat(32_001)]) {
    await assert.rejects(loadOVPersonality(memoryClient({ 'viking://~/memories/identity/personalities/outgoing.md': text })), /nonempty text/);
  }
  await assert.rejects(loadOVPersonality(memoryClient(), { personality: '../reserved' }), /Unknown personality/);
});

test('reserved retains ordered addressing, continuation, silence and freshness instructions', async () => {
  const text = await loadOVPersonality(memoryClient(), { personality: 'reserved' });
  const steps = [...text.matchAll(/^\d\. /gm)].map((m) => Number(m[0].slice(0, 1)));
  assert.deepEqual(steps, [1, 2, 3, 4, 5, 6]);
  assert.match(text, /same thread/);
  assert.match(text, /nobody intervening/);
  assert.match(text, /Continuation ends/);
  assert.match(text, /Messages you do not answer still enter your context/);
  assert.match(text, /freshness checks/);
});

test('cached OV reads survive outages but never changed selections, connections or rejected credentials', async (t) => {
  const os = await import('node:os');
  const { OVUnavailableError } = await import('../src/ov-connection.mjs');
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-personality-'));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  const openVikingConfigPath = path.join(dataDir, 'ov.json');
  fs.writeFileSync(openVikingConfigPath, JSON.stringify({ url: 'https://ov.example', api_key: 'one' }));
  const config = { dataDir, openVikingConfigPath, personality: 'reserved', agentName: 'Before' };
  await loadOVPersonality(memoryClient(), config);
  const unavailable = new OVUnavailableError('temporarily offline', 503);
  const client = { read: async () => { throw unavailable; } };
  let cached = 0;
  const text = await loadOVPersonality(client, { ...config, agentName: 'After' }, { onCached: () => cached++ });
  assert.equal(cached, 1);
  assert.match(text, /### 沉静内敛/);
  assert.match(text, /"After"/);
  assert.doesNotMatch(text, /"Before"/);
  await assert.rejects(loadOVPersonality(client, { ...config, personality: 'outgoing' }), (error) => error === unavailable);
  for (const status of [401, 403]) {
    await assert.rejects(loadOVPersonality({ read: async () => { throw new OVUnavailableError('rejected', status); } }, config), /rejected/);
  }
  fs.writeFileSync(openVikingConfigPath, JSON.stringify({ url: 'https://ov.example', api_key: 'two' }));
  await assert.rejects(loadOVPersonality(client, config), (error) => error === unavailable);
});

test('the OV HTTP client reads stored content and strips memory metadata before injection', async (t) => {
  const os = await import('node:os');
  const { OVSessionClient } = await import('../src/ov-session-context.mjs');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-identity-http-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const file = path.join(directory, 'ov.json');
  fs.writeFileSync(file, JSON.stringify({ url: 'https://ov.example', api_key: 'test-key' }));
  let stored;
  const client = new OVSessionClient(file, { fetch: async (url, init) => {
    assert.equal(init.headers['X-API-Key'], 'test-key');
    const parsed = new URL(url);
    if (parsed.pathname === '/api/v1/content/write') {
      stored = JSON.parse(init.body);
      assert.equal(stored.uri, 'viking://~/memories/identity/personalities/reserved.md');
      return Response.json({ status: 'ok', result: {} });
    }
    assert.equal(parsed.pathname, '/api/v1/content/read');
    assert.equal(parsed.searchParams.get('raw'), 'true');
    assert.equal(parsed.searchParams.get('limit'), '-1');
    return stored ? Response.json({ status: 'ok', result: `${stored.content}\n<!-- MEMORY_FIELDS\n{"version":1}\n-->` })
      : Response.json({ status: 'error' }, { status: 404 });
  } });
  const text = await loadOVPersonality(client, { personality: 'reserved' });
  assert.equal(text, stored.content.replaceAll('{{AGENT_NAME}}', 'the bot').trim());
  assert.doesNotMatch(text, /MEMORY_FIELDS/);
});


test('collective requests survive reserved filtering and both presets override duplicate-answer silence', async () => {
  for (const personality of PERSONALITIES) {
    const personalityPrompt = await loadOVPersonality(memoryClient(), { personality });
    const injected = assembleConversationPolicy({ personalityPrompt });
    assert.ok(injected.includes('所有agent都发表下看法'));
    assert.ok(injected.includes('每个agent都要回答'));
    assert.match(injected, /(?:Do not skip yours just because another|even if another)\n? ?agent/);
    assert.match(injected, /not an\ninstruction to adopt it yourself/);
  }
  const reserved = await loadOVPersonality(memoryClient(), { personality: 'reserved' });
  for (const example of ['所有 agent 都说说看法', '所有机器人都回答', '只限agent回答', '仅限 agent 回答']) {
    assert.ok(reserved.includes(example), example);
  }
  assert.ok(reserved.indexOf('3. The message explicitly calls') < reserved.indexOf('5. It addresses @everyone'));
  assert.match(reserved, /regardless of whether the requester is the owner/);
  assert.match(reserved, /bare @everyone\n   announcement, a quotation, or merely discussing agents does not count/);
  assert.match(reserved, /a question they address to someone else does not/);
  assert.match(reserved, /until completed, withdrawn,/);
});
