import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, ok } from './helpers/console-dom.mjs';
import { act, click, type } from './helpers/command-console-dom.mjs';

test('slash palette uses the built-in group order and keeps cross-group commands selectable', async () => {
  const host = await mount(
    async () => assert.fail('Opening the palette does not execute commands'),
    {
      locale: 'zh-CN',
    },
  );
  try {
    await type(host.input, '/');
    const groups = [
      ...host.container.querySelectorAll(
        '.ov-command-console__suggestion-group',
      ),
    ];
    const labels = host.container.querySelectorAll(
      '.ov-command-console__suggestion-label',
    );
    assert.equal(
      Array.from(labels, (label) => label.textContent).join(' / '),
      '核心流程 / 文件系统 / 搜索与上下文 / 文件传输 / 会话 / 配置与状态',
    );
    const names = (group) =>
      [...group.querySelectorAll('.ov-command-console__suggestion-name')].map(
        (name) => name.textContent,
      );
    assert.deepEqual(names(groups[0]), [
      'add-resource',
      'add-skill',
      'compile create',
      'compile',
      'find',
      'read',
      'write',
      'add-memory',
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
    const compile = options.find(
      (option) =>
        option.querySelector('.ov-command-console__suggestion-name')
          ?.textContent === 'compile',
    );
    await act(() => compile.focus());
    const commands = [
      ...host.container.querySelectorAll(
        '.ov-command-console__hint-steps button',
      ),
    ];
    assert.deepEqual(
      commands.map((button) => button.textContent),
      ['ov compile --compile-name <name>', 'ov task status <task_id>'],
    );
    await click(commands[0]);
    assert.equal(host.input.value, commands[0].textContent);
    await type(host.input, '/');
    const searchFind = host.container
      .querySelectorAll('.ov-command-console__suggestion-group')[2]
      .querySelector('[role="option"]');
    await click(searchFind);
    assert.equal(host.input.value, 'ov find ');
    await host.execute('ov help');
    assert.deepEqual(
      [
        ...host.container.querySelectorAll(
          '.ov-command-console__help-section-label',
        ),
      ].map((label) => label.textContent),
      [
        '核心流程',
        '文件系统',
        '搜索与上下文',
        '文件传输',
        '会话',
        '配置与状态',
      ],
    );
  } finally {
    await host.dispose();
  }
});

test('Console exposes the terminal empty state and lets the host replace its quick commands', async () => {
  const host = await mount(async () => ok({ state: 'ready' }));
  try {
    const names = () =>
      [
        ...host.container.querySelectorAll('.ov-command-console__empty-name'),
      ].map((element) => element.textContent);
    assert.deepEqual(names(), [
      'add-resource',
      'add-skill',
      'add-memory',
      'find',
      'compile create',
      'compile',
    ]);
    assert.equal(
      host.container.querySelector('.ov-command-console__empty-title')
        .textContent,
      'OpenViking CLI',
    );

    const pick = (name) =>
      click(
        [
          ...host.container.querySelectorAll(
            '.ov-command-console__empty-action',
          ),
        ].find(
          (button) =>
            button.querySelector('.ov-command-console__empty-name')
              ?.textContent === name,
        ),
      );
    await pick('add-resource');
    assert.match(host.input.value, /^ov add-resource https:\/\//);
    await pick('compile create');
    assert.equal(
      host.input.value,
      'ov compile create --compile-name <name> --from <source-uri> --to <target-uri> --skill <skill-uri>',
    );
    await pick('compile');
    assert.equal(host.input.value, 'ov compile --compile-name <name>');
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
    assert.equal(
      host.container.querySelector('.ov-command-console__empty'),
      null,
    );
  } finally {
    await host.dispose();
  }
});
