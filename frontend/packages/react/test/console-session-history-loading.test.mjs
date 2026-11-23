import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { act, createElement as h } from 'react';
import { VikingBot } from '@openviking/react/components/viking-bot';
import { mount, settle } from './helpers/module-dom.mjs';

const { useConversation } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'console-conversation.js'))
);

const click = (node) => act(async () => node.click());
const message = (id, text) => ({
  id,
  role: 'assistant',
  parts: [{ type: 'text', text, format: 'plain' }],
});

async function mountHistory(loadMessages) {
  const sessions = ['first', 'second', 'third'].map((id) => ({
    id,
    title: id,
  }));
  const service = {
    loadMessages,
    listSessions: async () => sessions,
    createSession: async () => ({ id: 'new', title: 'New' }),
    removeSession: async () => {},
    async *stream() {},
  };
  function HistoryProbe() {
    const view = useConversation(
      { locale: 'en-US', chatOptions: { sessionId: 'first', service } },
      () => {},
    );
    // The default list uses Ark's browser-only renderer. Keep the real welcome
    // and history UI, and render loaded message text inside the list slot.
    const loadedMessages = view.messages.length
      ? h(
          'div',
          null,
          view.messages.flatMap(({ parts }) => parts.map(({ text }) => text)),
        )
      : undefined;
    return h(VikingBot, {
      locale: 'en-US',
      sessions: view.sessions,
      messages: [],
      messageList: view.messageList ?? loadedMessages,
    });
  }
  const view = await mount(h(HistoryProbe));
  return {
    ...view,
    select: async (title) => {
      await click(
        view.container.querySelector('[aria-label="Session history"]'),
      );
      const option = [
        ...document.querySelectorAll('[role="option"], [role="menuitem"]'),
      ].find((item) => item.textContent === title);
      assert.ok(option, title);
      await click(option);
    },
    history: () =>
      view.container.querySelector(
        '.ov-viking-bot__list--loading, .ov-console__history-state',
      ),
    text: () =>
      view.container.querySelector('.ov-viking-bot__body').textContent,
  };
}

test('switching sessions shows loading until the active history arrives', async () => {
  const second = Promise.withResolvers();
  const third = Promise.withResolvers();
  const view = await mountHistory((id) => {
    if (id === 'first') return Promise.resolve([message(id, 'First history')]);
    return id === 'second' ? second.promise : third.promise;
  });
  try {
    assert.match(view.text(), /First history/);
    await view.select('second');
    assert.equal(view.history()?.getAttribute('role'), 'status');
    assert.equal(view.history()?.textContent, 'Loading conversation…');
    assert.doesNotMatch(view.text(), /First history|Welcome to VikingBot/);

    await view.select('third');
    await act(async () => second.resolve([message('second', 'Stale history')]));
    assert.equal(view.history()?.textContent, 'Loading conversation…');
    assert.doesNotMatch(view.text(), /Stale history|Welcome to VikingBot/);

    await act(async () => third.resolve([message('third', 'Third history')]));
    await settle();
    assert.equal(view.history(), null);
    assert.match(view.text(), /Third history/);
    assert.doesNotMatch(view.text(), /Stale history/);
  } finally {
    await view.unmount();
  }
});

test('history loading failure can be retried and an empty result shows the welcome view', async () => {
  let attempts = 0;
  const retry = Promise.withResolvers();
  const view = await mountHistory(async (id) => {
    if (id === 'first') return [];
    if (++attempts === 1) throw new Error('History unavailable');
    return retry.promise;
  });
  try {
    assert.match(view.text(), /Welcome to VikingBot/);
    await view.select('second');
    assert.match(view.text(), /Could not load the conversation/);
    assert.doesNotMatch(view.text(), /Welcome to VikingBot/);
    await click(view.history().querySelector('button'));
    assert.equal(attempts, 2);
    assert.equal(view.history()?.textContent, 'Loading conversation…');
    await act(async () => retry.resolve([]));
    assert.equal(view.history(), null);
    assert.match(view.text(), /Welcome to VikingBot/);
  } finally {
    await view.unmount();
  }
});
