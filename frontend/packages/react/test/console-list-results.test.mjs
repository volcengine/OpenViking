import assert from 'node:assert/strict';
import { test } from 'node:test';
import { act } from './helpers/command-console-dom.mjs';
import { mount, ok } from './helpers/console-dom.mjs';

function rows(record) {
  return [...record.querySelectorAll('.ov-command-console__search-item')];
}

test('task list renders each task and a localized empty result', async () => {
  const host = await mount(
    async (url) =>
      ok(
        new URL(url).searchParams.has('status')
          ? []
          : [
              {
                task_id: 'failed-task',
                task_type: 'compile',
                status: 'failed',
                updated_at_iso: '2026-11-20T07:32:00+00:00',
                error: 'Source is missing',
              },
              {
                task_id: 'finished-task',
                task_type: 'add_resource',
                status: 'completed',
              },
            ],
      ),
    { locale: 'zh-CN' },
  );
  try {
    await host.execute('ov task list');
    const items = rows(host.records.at(-1));
    assert.equal(items.length, 2);
    for (const value of [
      'failed-task',
      'compile',
      'failed',
      '2026-11-20',
      'Source is missing',
    ]) {
      assert.ok(items[0].textContent.includes(value));
    }
    assert.match(items[1].textContent, /finished-task/);
    assert.match(items[1].textContent, /add_resource/);
    assert.match(items[1].textContent, /completed/);

    await host.execute('ov task list --status running');
    assert.match(host.records.at(-1).textContent, /暂无任务/);
    await host.render({ locale: 'en-US' });
    assert.match(host.records.at(-1).textContent, /No tasks/);
    assert.doesNotMatch(host.records.at(-1).textContent, /暂无任务/);
  } finally {
    await host.dispose();
  }
});

test('grep renders linked hits with line context and reports result limits', async () => {
  const uri = 'viking://resources/demo/notes.md';
  const opened = [];
  const onOpenUri = (value) => opened.push(value);
  const host = await mount(
    async (_url, init) =>
      ok(
        JSON.parse(init.body).pattern === 'missing'
          ? { matches: [] }
          : {
              limit_reached: true,
              matches: [
                {
                  uri,
                  line: 7,
                  content: '  OpenViking',
                  before_context: [{ line: 6, content: 'Heading' }],
                  after_context: [{ line: 8, content: 'Details' }],
                  tags: ['guide', 'very-long-unbroken-resource-tag'],
                },
                { uri, line: 12, content: 'OpenViking example' },
              ],
            },
      ),
    { locale: 'en-US', onOpenUri },
  );
  try {
    await host.execute('ov grep OpenViking -b 1 -a 1');
    const record = host.records.at(-1);
    const items = rows(record);
    assert.equal(items.length, 2);
    assert.match(record.textContent, /More matches may exist/);
    assert.equal(
      items[0].querySelector('.ov-command-console__search-content').textContent,
      '6: Heading\n7:   OpenViking\n8: Details',
    );
    assert.equal(
      items[0].querySelector('.ov-command-console__search-tags').textContent,
      'guide, very-long-unbroken-resource-tag',
    );
    assert.match(items[1].textContent, /:12.*OpenViking example/);
    const link = items[0].querySelector(`a[href="${uri}"]`);
    assert.ok(link);
    await act(() => link.click());
    assert.deepEqual(opened, [uri]);

    await host.render({ locale: 'zh-CN', onOpenUri });
    const localized = host.records.at(-1);
    assert.match(localized.textContent, /已达到搜索上限/);
    assert.doesNotMatch(localized.textContent, /More matches may exist/);
    assert.equal(rows(localized).length, 2);

    await host.execute('ov grep missing');
    const empty = host.records.at(-1);
    assert.equal(rows(empty).length, 0);
    assert.ok(empty.querySelector('.ov-command-console__no-result'));
  } finally {
    await host.dispose();
  }
});
