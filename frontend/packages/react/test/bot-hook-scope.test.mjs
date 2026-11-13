import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement, StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { OpenVikingProvider } from './helpers/internal-hooks.mjs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const { useVikingBotChat } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-chat-hook.js'))
);
import { act } from './helpers/command-console-dom.mjs';

test('commercial service without interrupt has no stop capability; identity changes cancel its active owner', async () => {
  let chat;
  let release;
  let streamSignal;
  const pending = new Promise((resolve) => {
    release = resolve;
  });
  const service = {
    createSession: async () => ({ id: 'commercial', title: 'commercial' }),
    loadMessages: async () => [],
    async *stream(_input, signal) {
      streamSignal = signal;
      await pending;
      yield { type: 'complete', text: 'late' };
    },
  };
  function Host() {
    chat = useVikingBotChat({ service });
    return null;
  }
  const root = createRoot(document.createElement('div'));
  const render = (userId) =>
    act(() =>
      root.render(
        createElement(
          StrictMode,
          null,
          createElement(OpenVikingProvider, { userId }, createElement(Host)),
        ),
      ),
    );
  try {
    await render('alice');
    assert.equal(chat.canInterrupt, false);
    let sending;
    await act(() => {
      sending = chat.send('question');
    });
    assert.equal(chat.status, 'streaming');
    await render('bob');
    assert.equal(streamSignal.aborted, true);
    await act(async () => {
      release();
      assert.equal(await sending, false);
    });
    assert.deepEqual(chat.messages, []);
    assert.equal(chat.sessionId, undefined);
  } finally {
    await act(() => root.unmount());
  }
});
