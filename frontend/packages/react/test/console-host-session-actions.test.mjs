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

for (const target of ['second', undefined])
  test(`host navigation to ${target ?? 'new'} invalidates the old send's draft callback`, async () => {
    let view, finish;
    let draft = 'old session question';
    const gate = new Promise((resolve) => {
      finish = resolve;
    });
    const service = {
      createSession: async () => ({ id: 'created', title: 'created' }),
      loadMessages: async () => [],
      listSessions: async () => [],
      removeSession: async () => {},
      steer: async () => ({ accepted: true }),
      async *stream() {
        await gate;
        yield { type: 'complete', text: 'old answer' };
      },
    };
    function Host({ currentId }) {
      view = useConversation(
        {
          chatOptions: { service },
          conversation: {
            sessions: { items: [], currentId, onSelect() {}, onNew() {} },
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
      await act(async () => {
        root.render(createElement(Host, { currentId: 'first' }));
        await tick();
      });
      draft = 'old session question';
      await act(async () => {
        view.onSend({ text: draft, attachments: [] });
        await tick();
      });
      assert.equal(draft, '');
      await act(async () => {
        root.render(createElement(Host, { currentId: target }));
        await tick();
      });
      assert.equal(view.sessions.currentId, target);
      await act(async () => {
        finish();
        await tick();
      });
      assert.equal(view.sessions.currentId, target);
      assert.equal(draft, '');
    } finally {
      finish();
      await act(() => root.unmount());
    }
  });

for (const state of ['empty', 'selected', 'creating'])
  test(`each explicit New notifies the host once (${state}, StrictMode)`, async () => {
    let view, finishCreation;
    let notifications = 0;
    const selections = [];
    const gate = new Promise((resolve) => {
      finishCreation = resolve;
    });
    const service = {
      createSession: async () => {
        await gate;
        return { id: 'created', title: 'created' };
      },
      loadMessages: async () => [],
      listSessions: async () => [],
      removeSession: async () => {},
      steer: async () => ({ accepted: true }),
      async *stream() {
        yield { type: 'complete', text: 'answer' };
      },
    };
    function Host() {
      view = useConversation(
        {
          chatOptions: { service },
          conversation: {
            sessions: {
              items: [],
              currentId: state === 'selected' ? 'first' : undefined,
              onSelect: (id) => selections.push(id),
              onNew: () => notifications++,
            },
          },
        },
        () => {},
      );
      return null;
    }
    const root = createRoot(document.createElement('div'));
    try {
      await act(async () => {
        root.render(createElement(StrictMode, null, createElement(Host)));
        await tick();
      });
      assert.equal(notifications, 0);
      if (state === 'creating')
        await act(async () => {
          view.onSend({ text: 'pending creation', attachments: [] });
          await tick();
        });
      await act(() => view.sessions.onNew());
      assert.equal(notifications, 1);
      await act(() => view.sessions.onNew());
      assert.equal(notifications, 2);
      await act(async () => {
        finishCreation();
        await tick();
      });
      assert.deepEqual(selections, []);
      assert.equal(notifications, 2);
    } finally {
      finishCreation();
      await act(() => root.unmount());
    }
  });
