import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createElement, StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { act } from './helpers/command-console-dom.mjs';
const { useConversation } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'console-conversation.js'))
);
const tick = () => new Promise((resolve) => setImmediate(resolve));
for (const native of [false, true])
  test(`whitespace-normalized submission clears only matching draft (native=${native})`, async () => {
    let view, release;
    let draft = ' question ';
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    const service = {
      createSession: async () => ({ id: 's', title: 's' }),
      loadMessages: async () => [],
      listSessions: async () => [],
      removeSession: async () => {},
      steer: native ? async () => ({ accepted: true }) : undefined,
      async *stream() {
        await gate;
        yield { type: 'complete', text: 'answer' };
      },
    };
    function Host() {
      view = useConversation({ chatOptions: { service } }, (next) => {
        draft = typeof next === 'function' ? next(draft) : next;
      });
      return null;
    }
    const root = createRoot(document.createElement('div'));
    try {
      await act(() => root.render(createElement(Host)));
      await act(async () => {
        view.onSend({ text: 'question', attachments: [] });
        await tick();
      });
      if (native) assert.equal(draft, '');
      await act(async () => {
        release();
        await tick();
      });
      assert.equal(draft, '');
    } finally {
      await act(() => root.unmount());
    }
  });
for (const change of ['scope', 'service'])
  for (const success of [false, true])
    test(`old ${change} completion cannot restore or clear new draft (success=${success})`, async () => {
      let view, release;
      let draft = 'question';
      const gate = new Promise((resolve) => {
        release = resolve;
      });
      const service = {
        createSession: async () => ({ id: 's', title: 's' }),
        loadMessages: async () => [],
        listSessions: async () => [],
        removeSession: async () => {},
        steer: success ? undefined : async () => ({ accepted: true }),
        async *stream() {
          await gate;
          if (!success) throw new Error('old request');
          yield { type: 'complete', text: 'old answer' };
        },
      };
      const other = { ...service };
      function Host({ changed }) {
        view = useConversation(
          {
            chatOptions: {
              service: changed && change === 'service' ? other : service,
              scopeKey: change === 'scope' && changed ? 'new' : 'old',
            },
          },
          (next) => {
            draft = typeof next === 'function' ? next(draft) : next;
          },
        );
        return null;
      }
      const root = createRoot(document.createElement('div'));
      try {
        await act(() =>
          root.render(
            createElement(
              StrictMode,
              null,
              createElement(Host, { changed: false }),
            ),
          ),
        );
        await act(async () => {
          view.onSend({ text: 'question', attachments: [] });
          await tick();
        });
        await act(() =>
          root.render(
            createElement(
              StrictMode,
              null,
              createElement(Host, { changed: true }),
            ),
          ),
        );
        draft = success ? 'question' : '';
        await act(async () => {
          release();
          await tick();
        });
        assert.equal(draft, success ? 'question' : '');
      } finally {
        await act(() => root.unmount());
      }
    });
