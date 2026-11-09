import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  VikingBot,
  VikingBotSessionActions,
  groupByActivity,
} from '@openviking/react/components/viking-bot';
import { act, click } from './helpers/tui-dom.mjs';
import './helpers/console-dom.mjs';

test('explicit null empty state suppresses welcome and custom message lists always replace the body', () => {
  for (const props of [
    { emptyState: null },
    { messageList: null },
    { emptyState: null, messageList: createElement('p', null, 'Host list') },
  ]) {
    const html = renderToStaticMarkup(
      createElement(VikingBot, { messages: [], ...props }),
    );
    assert.doesNotMatch(html, /Welcome to VikingBot/);
    if (props.messageList) assert.match(html, /Host list/);
  }
});

test('activity blocks keep independent tool results separate in either arrival order', () => {
  const reasoning = { type: 'reasoning', text: 'Reasoning', groupId: 'g' };
  const result = {
    type: 'tool_result',
    id: 'orphan',
    name: 'search',
    output: 'Result body',
    groupId: 'g',
  };
  for (const parts of [
    [reasoning, result],
    [result, reasoning],
  ]) {
    assert.deepEqual(
      groupByActivity(parts).map((block) => block.parts),
      parts.map((part) => [part]),
    );
  }
});

test('shortcuts replace the owned prompt and preserve custom text before and after it', async () => {
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  let setDraft;
  let draft;
  function Host() {
    [draft, setDraft] = useState('Custom text');
    return createElement(VikingBot, {
      messages: [],
      draft,
      onDraftChange: setDraft,
      composer: createElement('div'),
      quickInputs: [
        { id: 'a', label: 'Prompt A', prompt: 'PROMPT_A' },
        { id: 'b', label: 'Prompt B', prompt: 'PROMPT_B' },
      ],
    });
  }
  try {
    await act(() => root.render(createElement(Host)));
    const select = (label) =>
      click(
        [...container.querySelectorAll('button')].find(
          (button) => button.textContent === label,
        ),
      );
    await select('Prompt A');
    await act(() => setDraft(`${draft}\nMore custom text`));
    await select('Prompt B');
    await select('Prompt B');
    assert.equal(draft, 'Custom text\nPROMPT_B\nMore custom text');
    await select('Prompt A');
    assert.equal(draft, 'Custom text\nPROMPT_A\nMore custom text');
  } finally {
    await act(() => root.unmount());
    container.remove();
  }
});

test('fallback session history notifies directly from opening events, not callback changes', async () => {
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  let calls = 0;
  const render = () =>
    act(() =>
      root.render(
        createElement(VikingBotSessionActions, {
          sessions: {
            items: [],
            onOpenHistory: () => {
              calls++;
            },
            onSelect() {},
            onNew() {},
          },
        }),
      ),
    );
  try {
    await render();
    const history = container.querySelector('button[aria-haspopup]');
    await click(history);
    assert.equal(calls, 1);
    await render();
    assert.equal(calls, 1);
    await click(history);
    await click(history);
    assert.equal(calls, 2);
  } finally {
    await act(() => root.unmount());
    container.remove();
  }
});

const quickInputs = [
  { id: 'a', label: 'Prompt A', prompt: 'PROMPT_A' },
  { id: 'b', label: 'Prompt B', prompt: 'PROMPT_B' },
];

// Opaque host replacements must preserve user-authored duplicate text.
for (const [name, edited, expected] of [
  ['inline suffix', 'PROMPT_A with my details', 'PROMPT_B with my details'],
  ['prefix', 'My details\nPROMPT_A', 'My details\nPROMPT_B'],
  [
    'later mention',
    'PROMPT_A\nReference to PROMPT_A here',
    'PROMPT_B\nReference to PROMPT_A here',
  ],
  ['edited owned text', 'PROMXT_A', 'PROMXT_A\nPROMPT_B'],
  [
    'ambiguous identical insertion',
    'PROMPT_A\nPROMPT_A',
    'PROMPT_A\nPROMPT_A\nPROMPT_B',
  ],
]) {
  test(`external draft changes preserve custom text: ${name}`, async () => {
    const container = document.createElement('div');
    document.body.append(container);
    const root = createRoot(container);
    let draft;
    let setDraft;
    function Host() {
      [draft, setDraft] = useState('');
      return createElement(VikingBot, {
        messages: [],
        draft,
        onDraftChange: setDraft,
        quickInputs,
        composer: createElement('div'),
      });
    }
    const select = (label) =>
      click(
        [...container.querySelectorAll('button')].find(
          (button) => button.textContent === label,
        ),
      );
    try {
      await act(() => root.render(createElement(Host)));
      await select('Prompt A');
      await act(() => setDraft(edited));
      await select('Prompt B');
      assert.equal(draft, expected);
      await select('Prompt B');
      assert.equal(draft, expected, 'reselecting does not accumulate prompts');
    } finally {
      await act(() => root.unmount());
      container.remove();
    }
  });
}
