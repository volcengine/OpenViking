import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, ok } from './helpers/console-dom.mjs';
import { click, type } from './helpers/tui-dom.mjs';

test('slash palette uses the Volcengine group order and keeps cross-group commands selectable', async () => {
  const host = await mount(
    async () => assert.fail('Opening the palette does not execute commands'),
    {
      locale: 'zh-CN',
    },
  );
  try {
    await type(host.input, '/');
    const groups = [
      ...host.container.querySelectorAll('.ov-tui__suggestion-group'),
    ];
    assert.deepEqual(
      groups.map(
        (group) => group.querySelector('.ov-tui__suggestion-label').textContent,
      ),
      ['核心流程', '文件系统', '搜索与上下文', '配置与状态'],
    );
    const names = (group) =>
      [...group.querySelectorAll('.ov-tui__suggestion-name')].map(
        (name) => name.textContent,
      );
    assert.deepEqual(names(groups[0]), [
      'add-resource',
      'add-skill',
      'compile',
      'find',
      'read',
      'write',
      'add-memory',
      'compile create',
    ]);
    assert.deepEqual(names(groups[1]), [
      'ls',
      'tree',
      'mkdir',
      'rm',
      'mv',
      'stat',
      'cp',
    ]);
    assert.deepEqual(names(groups[2]), [
      'find',
      'search',
      'grep',
      'glob',
      'abstract',
      'overview',
      'read',
    ]);
    const options = [...host.container.querySelectorAll('[role="option"]')];
    assert.equal(
      new Set(options.map((option) => option.id)).size,
      options.length,
    );
    const searchFind = groups[2].querySelector('[role="option"]');
    await click(searchFind);
    assert.equal(host.input.value, 'ov find ');
    await host.execute('ov help');
    assert.deepEqual(
      [...host.container.querySelectorAll('.ov-tui__help-section-label')].map(
        (label) => label.textContent,
      ),
      ['核心流程', '文件系统', '搜索与上下文', '配置与状态'],
    );
  } finally {
    await host.dispose();
  }
});

test('Console exposes the terminal empty state and lets the host replace its quick commands', async () => {
  const host = await mount(async () => ok({ state: 'ready' }));
  try {
    const names = () =>
      [...host.container.querySelectorAll('.ov-tui__empty-name')].map(
        (element) => element.textContent,
      );
    assert.deepEqual(names(), [
      'add-resource',
      'add-skill',
      'add-memory',
      'find',
      'compile',
    ]);
    assert.equal(
      host.container.querySelector('.ov-tui__empty-title').textContent,
      'OpenViking CLI',
    );

    const pick = (name) =>
      click(
        [...host.container.querySelectorAll('.ov-tui__empty-action')].find(
          (button) =>
            button.querySelector('.ov-tui__empty-name')?.textContent === name,
        ),
      );
    await pick('add-resource');
    assert.match(host.input.value, /^ov add-resource https:\/\//);
    await pick('compile');
    assert.equal(host.input.value, 'ov compile ');
    assert.equal(host.records.length, 0);

    await host.render({
      quickCommands: [
        { name: 'status', value: 'ov status   ' },
        { name: 'missing', value: '' },
        { name: 'help', value: '' },
      ],
    });
    assert.deepEqual(names(), ['status', 'help']);
    await pick('status');
    assert.equal(host.input.value, 'ov status   ');

    await host.render({ quickCommands: [] });
    assert.deepEqual(names(), []);
    assert.equal(host.container.querySelector('.ov-tui__empty'), null);
  } finally {
    await host.dispose();
  }
});
