import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { act } from './helpers/command-console-dom.mjs';
const { useConversation } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'console-conversation.js'))
);
const tick = () => new Promise((resolve) => setImmediate(resolve));

test('host session navigation reflects native creation without restarting its stream', async () => {
  let view;
  let finish;
  let notified;
  let loads = [];
  const gate = new Promise((resolve) => {
    finish = resolve;
  });
  const service = {
    createSession: async () => ({ id: 'created', title: 'created' }),
    loadMessages: async (id) => {
      loads.push(id);
      return [];
    },
    listSessions: async () => [],
    removeSession: async () => {},
    async *stream() {
      await gate;
      yield { type: 'complete', text: 'answer' };
    },
  };
  function Host({ currentId }) {
    view = useConversation(
      {
        chatOptions: { service },
        conversation: {
          sessions: {
            items: [{ id: 'external', title: 'Host title' }],
            currentId,
            onSelect: (id) => {
              notified = id;
            },
            onNew: () => {
              notified = 'new';
            },
          },
        },
      },
      () => {},
    );
    return null;
  }
  const root = createRoot(document.createElement('div'));
  try {
    await act(() => root.render(createElement(Host)));
    await act(async () => {
      view.onSend({ text: 'question', attachments: [] });
      await tick();
    });
    assert.equal(notified, 'created');
    await act(() => root.render(createElement(Host, { currentId: 'created' })));
    assert.equal(view.isSending, true);
    assert.deepEqual(loads, []);
    assert.equal(view.sessions.items[0].title, 'Host title');
    await act(async () => {
      finish();
      await tick();
    });
    assert.equal(view.messages.at(-1).parts[0].text, 'answer');
    await act(async () => {
      root.render(createElement(Host, { currentId: 'external' }));
      await tick();
    });
    assert.deepEqual(loads, ['external']);
    assert.equal(view.sessions.currentId, 'external');
    await act(() => view.sessions.onNew());
    assert.equal(notified, 'new');
  } finally {
    await act(() => root.unmount());
  }
});
