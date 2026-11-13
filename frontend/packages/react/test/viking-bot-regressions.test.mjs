import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  createElement,
  startTransition,
  StrictMode,
  Suspense,
  useState,
} from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  VikingBot,
  VikingBotSessionActions,
  groupByActivity,
} from '@openviking/react/components/viking-bot';
import { act, click } from './helpers/command-console-dom.mjs';
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

const selectShortcut = (container, label) =>
  click(
    [...container.querySelectorAll('button')].find(
      (button) => button.textContent === label,
    ),
  );

const quickInputs = [
  { id: 'a', label: 'Prompt A', prompt: 'PROMPT_A' },
  { id: 'b', label: 'Prompt B', prompt: 'PROMPT_B' },
];

// Opaque host replacements must preserve user-authored duplicate text.
for (const [name, edited, expected, initial = ''] of [
  [
    'surrounding text',
    'Custom text\nPROMPT_A\nMore custom text',
    'Custom text\nPROMPT_B\nMore custom text',
    'Custom text',
  ],
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
  ['committed clear then restore', ['', 'PROMPT_A'], 'PROMPT_A\nPROMPT_B'],
]) {
  test(`external draft changes preserve custom text: ${name}`, async () => {
    const container = document.createElement('div');
    document.body.append(container);
    const root = createRoot(container);
    let draft;
    let setDraft;
    function Host() {
      [draft, setDraft] = useState(initial);
      return createElement(VikingBot, {
        messages: [],
        draft,
        onDraftChange: setDraft,
        quickInputs,
        composer: createElement('div'),
      });
    }
    try {
      await act(() =>
        root.render(createElement(StrictMode, null, createElement(Host))),
      );
      await selectShortcut(container, 'Prompt A');
      for (const value of Array.isArray(edited) ? edited : [edited])
        await act(() => setDraft(value));
      await selectShortcut(container, 'Prompt B');
      assert.equal(draft, expected);
      await selectShortcut(container, 'Prompt B');
      assert.equal(draft, expected, 'reselecting does not accumulate prompts');
      await selectShortcut(container, 'Prompt A');
      assert.equal(draft, expected.replace('PROMPT_B', 'PROMPT_A'));
    } finally {
      await act(() => root.unmount());
      container.remove();
    }
  });
}

test('an abandoned controlled clear does not release the committed shortcut', async () => {
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  const pending = new Promise(() => {});
  let pause = false;
  let pausedRenders = 0;
  let draft;
  let setDraft;
  function Composer({ value }) {
    if (pause && !value) {
      pausedRenders++;
      throw pending;
    }
    return createElement('output', { 'aria-label': 'Committed draft' }, value);
  }
  function Host() {
    [draft, setDraft] = useState('');
    return createElement(
      Suspense,
      { fallback: 'Waiting' },
      createElement(VikingBot, {
        messages: [],
        draft,
        onDraftChange: setDraft,
        quickInputs,
        composer: createElement(Composer, { value: draft }),
      }),
    );
  }
  try {
    await act(() => root.render(createElement(Host)));
    await selectShortcut(container, 'Prompt A');
    pause = true;
    await act(() => startTransition(() => setDraft('')));
    assert.ok(pausedRenders > 0, 'the controlled clear rendered but suspended');
    assert.equal(container.querySelector('output').textContent, 'PROMPT_A');
    await selectShortcut(container, 'Prompt B');
    assert.equal(
      draft,
      'PROMPT_B',
      'the committed prompt still belongs to the shortcut',
    );
  } finally {
    await act(() => root.unmount());
    container.remove();
  }
});
