import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, ok } from './helpers/console-dom.mjs';

test('the five quick commands use scoped native requests and command-specific result cards', async () => {
  const calls = [];
  const root = 'viking://resources/notes';
  const responses = [
    { task_id: 'resource-task', root_uri: root, source_path: 'internal' },
    { root_uri: 'viking://agent/skills/wiki' },
    { message_count: 1 },
    { task_id: 'memory-task' },
    { resources: [{ uri: root, score: 0.95 }] },
    { task_id: 'compile-task', status: 'pending' },
    { to: 'viking://resources/wiki', auto_update: false },
  ];
  const host = await mount(async (url, init) => {
    calls.push([new URL(url).pathname, JSON.parse(init.body)]);
    return ok(responses[calls.length - 1]);
  });
  try {
    await host.execute('ov add-resource https://example.com/notes.md');
    assert.match(
      host.records.at(-1).querySelector('.ov-command-console__write')
        .textContent,
      /Task id: resource-task/,
    );
    assert.equal(
      host.records.at(-1).querySelector('a[href="viking://resources/notes"]')
        .textContent,
      root,
    );
    assert.doesNotMatch(host.records.at(-1).textContent, /internal/);
    await host.execute(
      'ov add-skill https://example.com/wiki -p viking://agent/skills',
    );
    assert.match(
      host.records.at(-1).querySelector('.ov-command-console__write')
        .textContent,
      /Root uri: viking:\/\/agent\/skills\/wiki/,
    );
    await host.execute('ov add-memory "Prefer tables" --session-id "test/a b"');
    assert.match(
      host.records.at(-1).querySelector('.ov-command-console__write')
        .textContent,
      /memory-task/,
    );
    await host.execute('ov find "notes"');
    assert.match(host.records.at(-1).textContent, /0\.950/);
    await host.execute(
      'ov compile --from viking://resources/notes --to viking://resources/wiki',
    );
    assert.match(
      host.records.at(-1).querySelector('.ov-command-console__write')
        .textContent,
      /compile-task/,
    );
    await host.execute(
      'ov compile create --from viking://resources/notes --to viking://resources/wiki',
    );
    assert.match(
      host.records.at(-1).querySelector('.ov-command-console__write')
        .textContent,
      /Auto update: false/,
    );
    assert.doesNotMatch(
      host.records.at(-1).querySelector('.ov-command-console__write')
        .textContent,
      /Task id/,
    );
    assert.deepEqual(calls, [
      ['/gateway/api/v1/resources', { path: 'https://example.com/notes.md' }],
      [
        '/gateway/api/v1/skills',
        {
          data: 'https://example.com/wiki',
          target_uri: 'viking://agent/skills',
        },
      ],
      [
        '/gateway/api/v1/sessions/test%2Fa%20b/messages',
        { role: 'user', content: 'Prefer tables' },
      ],
      ['/gateway/api/v1/sessions/test%2Fa%20b/commit', {}],
      ['/gateway/api/v1/search/find', { query: 'notes' }],
      [
        '/gateway/api/v1/compile',
        {
          from: [root],
          to: 'viking://resources/wiki',
          skill: 'viking://system/compile/example-skills/llm-wiki/SKILL.md',
        },
      ],
      [
        '/gateway/api/v1/compile',
        {
          from: [root],
          to: 'viking://resources/wiki',
          skill: 'viking://system/compile/example-skills/llm-wiki/SKILL.md',
          args: { auto_update: false },
        },
      ],
    ]);
    await host.execute('ov compile --from viking://resources/notes');
    assert.equal(host.records.at(-1).dataset.status, 'error');
    assert.equal(calls.length, 7);
  } finally {
    await host.dispose();
  }
});

test('Console passes live availability and session controls to the VikingBot view', async () => {
  const copied = [];
  const opened = [];
  const id = 'session_2026111100000_6c4ebf';
  const uri = 'viking://session/' + id;
  const conversation = {
    // Use the host slot so this Node test does not render Ark's browser composer.
    composer: false,
    onCopy: (text) => copied.push(text),
    sessions: {
      currentId: id,
      currentUri: uri,
      items: [],
      onSelect() {},
      onNew() {},
    },
  };
  const host = await mount(
    async () => assert.fail('VikingBot status belongs to the host'),
    {
      defaultMode: 'conversation',
      conversation: {
        ...conversation,
        isAvailable: false,
        unavailableMessage: 'Bot configuration failed',
      },
      onOpenUri: (value) => opened.push(value),
    },
  );
  try {
    const hint = () =>
      host.container.querySelector('.ov-viking-bot__unavailable');
    assert.equal(hint()?.textContent, 'Bot configuration failed');
    assert.equal(
      host.container.querySelector('.ov-viking-bot__session-meta code').title,
      id,
    );
    await host.press('Copy');
    await host.press('Locate session in the file browser');
    assert.deepEqual(copied, [id]);
    assert.deepEqual(opened, [uri]);
    await host.render({
      defaultMode: 'conversation',
      conversation: { ...conversation, isAvailable: true },
    });
    assert.equal(hint(), null);
  } finally {
    await host.dispose();
  }
});
