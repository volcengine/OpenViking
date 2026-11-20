import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, ok } from './helpers/console-dom.mjs';

test('the five quick commands use scoped native requests and command-specific result cards', async () => {
  const calls = [];
  const root = 'viking://resources/notes';
  const compileSkill = 'viking://system/compile/example-skills/llm-wiki';
  const responses = [
    { task_id: 'resource-task', root_uri: root, source_path: 'internal' },
    { root_uri: 'viking://agent/skills/wiki' },
    { message_count: 1 },
    { task_id: 'memory-task' },
    { resources: [{ uri: root, score: 0.95 }] },
    { compile_name: 'demo-wiki' },
    { task_id: 'compile-task', status: 'pending' },
    { task_id: 'compile-task', status: 'completed' },
    { task_id: 'one-off-compile', status: 'pending' },
  ];
  const host = await mount(
    async (url, init) => {
      calls.push([
        new URL(url).pathname,
        init?.body ? JSON.parse(init.body) : undefined,
      ]);
      return ok(responses[calls.length - 1]);
    },
    { taskService: { getTask: async (id) => ({ id, status: 'pending' }) } },
  );
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
      `ov compile create --compile-name demo-wiki --from ${root},viking://resources/report --from ${root} --from viking://resources/research --to viking://resources/wiki --skill ${compileSkill} --instruction "Keep sources" --args '{"model_name":"sample-model","auto_update":true,"update_schedule":{"frequency":"daily","hour":9,"minute":30}}'`,
    );
    assert.match(
      host.records.at(-1).querySelector('.ov-command-console__write')
        .textContent,
      /Compile name: demo-wiki/,
    );
    assert.doesNotMatch(
      host.records.at(-1).querySelector('.ov-command-console__write')
        .textContent,
      /Task id/,
    );
    await host.execute('ov compile --compile-name research-Wiki');
    assert.match(
      host.records.at(-1).querySelector('.ov-command-console__write')
        .textContent,
      /compile-task/,
    );
    await host.execute('ov task status compile-task');
    assert.match(host.records.at(-1).textContent, /completed/);
    await host.execute(
      'ov compile --from viking://resources/notes --to viking://resources/wiki',
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
        '/gateway/api/v1/compile/create',
        {
          compile_name: 'demo-wiki',
          from: [
            root,
            'viking://resources/report',
            'viking://resources/research',
          ],
          to: 'viking://resources/wiki',
          skill: compileSkill,
          instruction: 'Keep sources',
          args: {
            model_name: 'sample-model',
            auto_update: true,
            update_schedule: { frequency: 'daily', hour: 9, minute: 30 },
          },
        },
      ],
      ['/gateway/api/v1/compile', { compile_name: 'research-Wiki' }],
      ['/gateway/api/v1/tasks/compile-task', undefined],
      [
        '/gateway/api/v1/compile',
        {
          from: [root],
          to: 'viking://resources/wiki',
          skill: 'viking://system/compile/example-skills/llm-wiki/SKILL.md',
        },
      ],
    ]);
    await host.execute('ov compile --compile-name 研发-Wiki');
    assert.equal(host.records.at(-1).dataset.status, 'success');
    assert.deepEqual(calls.at(-1), [
      '/gateway/api/v1/compile',
      { compile_name: '研发-Wiki' },
    ]);
    for (const command of [
      'ov compile --from viking://resources/notes',
      'ov compile --compile-name demo-wiki --from viking://resources/notes',
      'ov compile --compile-name "invalid name"',
      `ov compile --compile-name ${'a'.repeat(32)}汉`,
      'ov compile create --compile-name demo-wiki --from viking://resources/notes --to viking://resources/wiki',
      `ov compile create --compile-name demo-wiki --from ${root} --to viking://resources/wiki --skill ${compileSkill} --args '[]'`,
    ]) {
      await host.execute(command);
      assert.equal(host.records.at(-1).dataset.status, 'error', command);
    }
    assert.equal(calls.length, 10);
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
    messages: [],
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
