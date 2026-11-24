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

for (const target of ['first', 'second'])
  test(`explicit history selection notifies once per action (${target}, StrictMode)`, async () => {
    let view;
    const selections = [];
    const loads = [];
    const service = {
      createSession: async () => ({ id: 'created', title: 'created' }),
      loadMessages: async (id) => {
        loads.push(id);
        return [];
      },
      listSessions: async () => [],
      removeSession: async () => {},
      async *stream() {
        yield { type: 'complete', text: 'answer' };
      },
    };
    function Host({ currentId }) {
      view = useConversation(
        {
          chatOptions: { service },
          conversation: {
            sessions: {
              items: [],
              currentId,
              onSelect: (id) => selections.push(id),
              onNew() {},
            },
          },
        },
        () => {},
      );
      return null;
    }
    const render = (id) =>
      createElement(StrictMode, null, createElement(Host, { currentId: id }));
    const root = createRoot(document.createElement('div'));
    try {
      await act(async () => {
        root.render(render('first'));
        await tick();
      });
      const initialLoads = loads.length;
      assert.deepEqual(selections, []);
      await act(async () => {
        view.sessions.onSelect(target);
        await tick();
      });
      assert.deepEqual(selections, [target]);
      assert.equal(loads.length, initialLoads + 1);
      await act(async () => {
        root.render(render(target));
        await tick();
      });
      assert.deepEqual(selections, [target]);
      assert.equal(loads.length, initialLoads + 1);
      await act(async () => {
        view.sessions.onSelect(target);
        await tick();
      });
      assert.deepEqual(selections, [target, target]);
      assert.equal(loads.length, initialLoads + 2);
    } finally {
      await act(() => root.unmount());
    }
  });
