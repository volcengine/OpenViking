import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount } from './helpers/console-dom.mjs';
import { act, click, type } from './helpers/command-console-dom.mjs';

test('Console shows the Compile guide and extended parameters in the selected locale', async () => {
  for (const [locale, example, help] of [
    [
      'en-US',
      'ov find "What is the value of OpenViking?"',
      /Use ov compile create to save a configuration/,
    ],
    [
      'zh-CN',
      'ov find "OpenViking 有什么价值？"',
      /使用 ov compile create 创建编译配置/,
    ],
  ]) {
    const host = await mount(
      async () => assert.fail('Examples and help are local to the Console'),
      { locale },
    );
    try {
      const find = [
        ...host.container.querySelectorAll('.ov-command-console__empty-action'),
      ].find(
        (button) =>
          button.querySelector('.ov-command-console__empty-name')
            ?.textContent === 'find',
      );
      await click(find);
      assert.equal(host.input.value, example);
      await type(host.input, '/compile');
      const compile = host.container.querySelector('[role="option"]');
      await act(() => compile.focus());
      const hint = host.container.querySelector('.ov-command-console__hint');
      assert.match(
        hint.querySelector('.ov-command-console__hint-usage').textContent,
        /\[--instruction <text>\] \[--args <json-object>\]/,
      );
      assert.deepEqual(
        [
          ...hint.querySelectorAll('table')[1].querySelectorAll('tbody code'),
        ].map((cell) => cell.textContent),
        [
          'model_name',
          'auto_update',
          'update_schedule',
          'update_schedule.frequency',
          'update_schedule.weekday',
          'update_schedule.hour',
          'update_schedule.minute',
        ],
      );
      await host.execute('ov help');
      assert.match(host.records.at(-1).textContent, help);
    } finally {
      await host.dispose();
    }
  }
});
