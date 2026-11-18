import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement, StrictMode } from 'react';
import { VikingBotSessionActions } from '@openviking/react/components/viking-bot';
import { act, click } from './helpers/command-console-dom.mjs';
import { mount, ok } from './helpers/console-dom.mjs';

const { createRoot } = await import('react-dom/client');
globalThis.HTMLIFrameElement = window.HTMLIFrameElement;

const session = { id: 'fixture', title: 'Fixture' };
const row = (name) =>
  [...document.querySelectorAll('[role="menuitem"]')].find(
    (item) => (item.getAttribute('aria-label') ?? item.textContent) === name,
  );
const dialog = () => document.querySelector('[role="dialog"]');
const submit = () =>
  act(() => {
    dialog()
      .querySelector('form')
      .dispatchEvent(
        new window.Event('submit', { bubbles: true, cancelable: true }),
      );
  });

const openDialog = async (history, action) => {
  await click(history);
  await click(row(`Session actions · ${session.title}`));
  await click(row(action));
  assert.ok(dialog());
};

function mountSessionActions() {
  const container = document.body.appendChild(document.createElement('div'));
  const root = createRoot(container);
  return {
    render: (overrides) =>
      act(() =>
        root.render(
          createElement(
            StrictMode,
            null,
            createElement(VikingBotSessionActions, {
              sessions: {
                items: [session],
                onNew() {},
                onSelect() {},
                ...overrides,
              },
            }),
          ),
        ),
      ),
    open: (action) =>
      openDialog(
        container.querySelector('button[aria-label="Session history"]'),
        action,
      ),
    async dispose() {
      await act(() => root.unmount());
      container.remove();
    },
  };
}

for (const action of ['Rename', 'Delete']) {
  test(`${action} confirmation closes when the host changes session scope`, async () => {
    const calls = [];
    const view = mountSessionActions();
    const render = (scopeKey) =>
      view.render({
        scopeKey,
        onRename: (id) => {
          calls.push([scopeKey, 'Rename', id]);
        },
        onRemove: (id) => {
          calls.push([scopeKey, 'Delete', id]);
        },
      });
    try {
      await render('first');
      await view.open(action);
      await render('first');
      assert.ok(dialog());

      await render('second');
      assert.ok(!dialog(), 'the previous scope’s confirmation must close');
      assert.deepEqual(calls, []);
      await render('first');
      assert.ok(!dialog(), 'switching back must not restore a confirmation');

      await render('second');
      await view.open(action);
      await submit();
      assert.deepEqual(calls, [['second', action, session.id]]);
    } finally {
      await view.dispose();
    }
  });

  test(`${action} confirmation closes when Console changes session services`, async () => {
    const calls = [];
    const props = (name) => ({
      scopeKey: 'console',
      chatOptions: {
        service: {
          listSessions: async () => [session],
          createSession: async () => session,
          loadMessages: async () => [],
          async *stream() {},
          renameSession: async (id) => {
            calls.push([name, 'Rename', id]);
          },
          removeSession: async (id) => {
            calls.push([name, 'Delete', id]);
          },
        },
      },
      conversation: { messageList: false, composer: false },
      defaultMode: 'conversation',
    });
    const view = await mount(() => ok([]), props('first'));
    try {
      await openDialog(view.button('Session history'), action);
      await view.render(props('second'));
      assert.ok(!dialog(), 'the previous service’s confirmation must close');
      assert.deepEqual(calls, []);

      await openDialog(view.button('Session history'), action);
      await submit();
      assert.deepEqual(calls, [['second', action, session.id]]);
    } finally {
      await view.dispose();
    }
  });
}

test('a completed action from the previous scope leaves the new confirmation open', async () => {
  const { promise, resolve } = Promise.withResolvers();
  const calls = [];
  const view = mountSessionActions();
  try {
    await view.render({ scopeKey: 'first', onRemove: () => promise });
    await view.open('Delete');
    await submit();
    assert.equal(
      dialog().querySelector('form').getAttribute('aria-busy'),
      'true',
    );

    await view.render({
      scopeKey: 'second',
      onRemove: (id) => {
        calls.push(id);
      },
    });
    assert.ok(!dialog());
    await view.open('Delete');
    const nextConfirmation = dialog();
    await act(() => resolve());
    assert.ok(dialog() === nextConfirmation);
    assert.deepEqual(calls, []);
  } finally {
    resolve();
    await view.dispose();
  }
});

test('replacement of a standalone handler dismisses a confirmation without a host scope', async () => {
  const view = mountSessionActions();
  try {
    await view.render({ onRemove() {} });
    await view.open('Delete');
    await view.render({ onRemove() {} });
    assert.ok(!dialog());
  } finally {
    await view.dispose();
  }
});

test('hosted Console dismisses confirmation when the Provider account changes', async () => {
  const calls = [];
  const props = {
    defaultMode: 'conversation',
    conversation: {
      messages: [],
      messageList: false,
      composer: false,
      sessions: {
        items: [session],
        onNew() {},
        onSelect() {},
        onRemove: (id) => {
          calls.push(id);
        },
      },
    },
  };
  const view = await mount(() => ok([]), props, { accountId: 'first' });
  try {
    await openDialog(view.button('Session history'), 'Delete');
    await view.render(props, { accountId: 'second' });
    assert.ok(!dialog(), 'switching accounts must dismiss the confirmation');
    assert.deepEqual(calls, []);
  } finally {
    await view.dispose();
  }
});
