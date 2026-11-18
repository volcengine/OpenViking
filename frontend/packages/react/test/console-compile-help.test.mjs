import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount } from './helpers/console-dom.mjs';
import { act, click, type } from './helpers/command-console-dom.mjs';

test('Console shows the Compile guide and extended parameters in the selected locale', async () => {
  for (const [locale, example, help, modelHelp, runtimeHelp] of [
    [
      'en-US',
      'ov find "What is the value of OpenViking?"',
      /Create and save a compile configuration/,
      /server Runtime configuration/,
      /built-in VikingBot Runtime uses the server's agent_loop provider and model[\s\S]*If the Runtime uses an Ark endpoint/,
    ],
    [
      'zh-CN',
      'ov find "OpenViking 有什么价值？"',
      /创建并保存编译配置/,
      /服务端 Runtime 配置决定/,
      /内置 VikingBot Runtime 使用服务端 agent_loop 的 provider 和 model[\s\S]*Runtime 使用方舟接入点时/,
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
      await type(host.input, '/compile create');
      const create = [
        ...host.container.querySelectorAll('[role="option"]'),
      ].find((option) => option.textContent.includes('compile create'));
      await act(() => create.focus());
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
      assert.match(hint.textContent, modelHelp);
      assert.match(hint.textContent, runtimeHelp);
      assert.doesNotMatch(hint.textContent, /doubao-2-1-lite/);
      assert.match(
        hint.textContent,
        locale === 'zh-CN'
          ? /有有效计划则为 true/
          : /true if a valid schedule is present/,
      );
      assert.match(
        hint.textContent,
        locale === 'zh-CN'
          ? /false 关闭定时自动执行，但保留已保存的 update_schedule/
          : /Set to false to disable scheduled runs while keeping the saved update_schedule/,
      );
      assert.match(hint.textContent, /00:00.{1,4}03:59/);
      await host.execute('ov help');
      assert.match(host.records.at(-1).textContent, help);
    } finally {
      await host.dispose();
    }
  }
});
