import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { VikingBot } from '@openviking/react/components/viking-bot';
import { act } from './helpers/command-console-dom.mjs';
import './helpers/console-dom.mjs';

const quickInputs = [
  { id: 'a', label: 'Prompt A', prompt: 'PROMPT_A' },
  { id: 'b', label: 'Prompt B', prompt: 'PROMPT_B' },
];

async function render(props) {
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  await act(() =>
    root.render(
      createElement(VikingBot, {
        draft: '',
        onDraftChange: () => {},
        quickInputs,
        ...props,
      }),
    ),
  );
  return {
    container,
    groups: () => [
      ...container.querySelectorAll('.ov-viking-bot__quick-inputs'),
    ],
    async unmount() {
      await act(() => root.unmount());
      container.remove();
    },
  };
}

// 内置输入区依赖的 Sender 在测试环境中不加载，这里只断言欢迎页一侧。
// The built-in composer's Sender does not load under test, so this covers the
// welcome side only.
test('the welcome leaves the quick inputs to the built-in composer', async () => {
  const view = await render({ messages: [] });
  try {
    assert.ok(view.container.querySelector('.ov-viking-bot__home'));
    assert.equal(
      view.container.querySelector(
        '.ov-viking-bot__home .ov-viking-bot__quick-inputs',
      ),
      null,
    );
  } finally {
    await view.unmount();
  }
});

test('the welcome keeps the quick inputs when the host supplies the composer', async () => {
  const view = await render({
    messages: [],
    composer: createElement('div'),
  });
  try {
    const groups = view.groups();
    assert.equal(groups.length, 1);
    assert.ok(groups[0].closest('.ov-viking-bot__home'));
  } finally {
    await view.unmount();
  }
});
