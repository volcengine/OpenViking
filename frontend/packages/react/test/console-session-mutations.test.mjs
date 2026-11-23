import assert from 'node:assert/strict';
import test from 'node:test';
import { act, click, key } from './helpers/command-console-dom.mjs';
import { mount, ok } from './helpers/console-dom.mjs';

globalThis.HTMLIFrameElement = window.HTMLIFrameElement;

const row = (name) =>
  [...document.querySelectorAll('[role="menuitem"]')].find(
    (item) => (item.getAttribute('aria-label') ?? item.textContent) === name,
  );
const dialog = () => document.querySelector('[role="dialog"]');
const button = (label) =>
  [...dialog().querySelectorAll('button')].find(
    (element) => element.textContent === label,
  );
const type = (input, value) =>
  act(() => {
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    ).set.call(input, value);
    input.dispatchEvent(new window.Event('input', { bubbles: true }));
  });
const submit = async () => {
  await act(() => {
    dialog()
      .querySelector('form')
      .dispatchEvent(
        new window.Event('submit', { bubbles: true, cancelable: true }),
      );
  });
};

test('shared Console renames provider-backed sessions without a host rename callback', async () => {
  let title = 'First';
  const requests = [];
  const service = {
    listSessions: async () => [
      { id: 'first/id', title, canRename: true },
      { id: 'second', title: 'Second' },
    ],
    createSession: async () => ({ id: 'new', title: 'New' }),
    loadMessages: async () => [],
    async *stream() {},
    removeSession: async () => {},
  };
  const view = await mount(
    (input, init) => {
      if (init.method !== 'PATCH') return ok([]);
      const body = JSON.parse(init.body);
      requests.push([new URL(input).pathname, body]);
      title = body.title;
      return ok({});
    },
    {
      chatOptions: { service },
      conversation: { messageList: false, composer: false },
      defaultMode: 'conversation',
    },
  );
  try {
    await view.press('Session history');
    await click(row('Session actions · Second'));
    assert.equal(row('Rename'), undefined);
    assert.ok(row('Delete'));
    await click(row('Session actions · First'));
    await click(row('Rename'));
    await type(dialog().querySelector('input[name="session-name"]'), 'Updated');
    await submit();
    assert.deepEqual(requests, [
      ['/gateway/api/v1/sessions/first%2Fid', { title: 'Updated' }],
    ]);
    await view.press('Session history');
    assert.ok(row('Updated'));
  } finally {
    await view.dispose();
  }
});

test('closing a session dialog with the mouse restores history focus without showing its tooltip', async () => {
  const view = await mount(() => ok([]), {
    defaultMode: 'conversation',
    conversation: {
      messages: [],
      messageList: false,
      composer: false,
      sessions: {
        items: [{ id: 'first', title: 'First' }],
        onNew() {},
        onSelect() {},
        onRename() {},
      },
    },
  });
  try {
    const history = view.button('Session history');
    await click(history);
    const actions = row('Session actions · First');
    if (actions) await click(actions);
    const rename = row(actions ? 'Rename' : 'Rename session “First”');
    assert.ok(rename);
    await click(rename);
    const cancel = button('Cancel');
    cancel.dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true }));
    await click(cancel);
    assert.equal(dialog(), null);
    await act(async () => new Promise((resolve) => setTimeout(resolve, 350)));
    assert.equal(document.activeElement, history);
    assert.equal(Boolean(document.querySelector('[role="tooltip"]')), false);
  } finally {
    await view.dispose();
  }
});

test('session history can rename a session and delete the active session after confirmation', async () => {
  let items = [
    { id: 'first', title: 'First' },
    { id: 'second', title: 'Second' },
  ];
  const calls = [];
  const historyLoads = [];
  const service = {
    listSessions: async () => items,
    createSession: async () => ({ id: 'new', title: 'New' }),
    loadMessages: async (id) => {
      historyLoads.push(id);
      return [];
    },
    async *stream() {},
    renameSession: async (id, title) => {
      calls.push(['rename', id, title]);
      items = items.map((item) => (item.id === id ? { ...item, title } : item));
    },
    removeSession: async (id) => {
      calls.push(['remove', id]);
      items = items.filter((item) => item.id !== id);
    },
  };
  const view = await mount(() => ok([]), {
    chatOptions: { service },
    conversation: { messageList: false, composer: false },
    defaultMode: 'conversation',
  });
  try {
    await key(view.button('Session history'), 'ArrowDown');
    assert.equal(document.activeElement === row('First'), true);
    await key(document.activeElement, 'ArrowDown');
    const actions = row('Session actions · First');
    assert.equal(document.activeElement, actions);
    await click(actions);
    await key(actions, 'ArrowDown');
    assert.equal(document.activeElement, row('Rename'));
    await key(document.activeElement, 'ArrowDown');
    assert.equal(document.activeElement, row('Delete'));
    await key(document.activeElement, 'Escape');
    assert.equal(row('Delete'), undefined);
    assert.equal(document.activeElement, actions);
    await click(actions);
    await click(row('Rename'));
    const name = dialog().querySelector('input[name="session-name"]');
    assert.equal(name.value, 'First');
    await type(name, ' ');
    await submit();
    assert.match(dialog().textContent, /Enter a session name/);
    assert.deepEqual(calls, []);
    await type(name, ' Updated ');
    await submit();
    assert.equal(dialog(), null);
    await view.press('Session history');
    await click(row('Updated'));
    assert.deepEqual(historyLoads, ['first']);
    assert.equal(
      view.container.querySelector('.ov-viking-bot__session-meta code')
        ?.textContent,
      'first',
    );

    await view.press('Session history');
    await click(row('Session actions · Updated'));
    await click(row('Delete'));
    await click(button('Cancel'));
    assert.deepEqual(calls, [['rename', 'first', 'Updated']]);
    await view.press('Session history');
    await click(row('Session actions · Updated'));
    await click(row('Delete'));
    await submit();
    assert.deepEqual(calls, [
      ['rename', 'first', 'Updated'],
      ['remove', 'first'],
    ]);
    assert.equal(
      Boolean(view.container.querySelector('.ov-viking-bot__session-meta')),
      false,
    );
    await view.press('Session history');
    assert.equal(row('Updated'), undefined);
    assert.ok(row('Second'));
  } finally {
    await view.dispose();
  }
});
