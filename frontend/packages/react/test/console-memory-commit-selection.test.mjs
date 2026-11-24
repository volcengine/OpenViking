import assert from 'node:assert/strict';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { act, createElement } from 'react';
import { mount, settle } from './helpers/module-dom.mjs';

const bundled = (name) =>
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, name));
const { useConversation } = await import(bundled('console-conversation.js'));

test('checks the latest reply when returning to a session with an older local submission', async () => {
  let view;
  let replyId = 'answer-a';
  let lookups = 0;
  const service = {
    listSessions: async () => [
      { id: 'demo', title: 'Demo' },
      { id: 'other', title: 'Other' },
    ],
    loadMessages: async (sessionId) =>
      sessionId === 'demo'
        ? [
            {
              id: replyId,
              role: 'assistant',
              parts: [{ type: 'text', text: replyId }],
            },
          ]
        : [],
    commitSession: async () => ({ status: 'accepted', taskId: 'task-a' }),
    getSessionCommitTask: async () => {
      lookups++;
      if (replyId === 'answer-b') return { id: 'task-b', status: 'completed' };
    },
  };
  const taskService = {
    getTask: async (id) => ({ id, status: 'completed' }),
  };
  function Host() {
    view = useConversation({ chatOptions: { service, taskService } }, () => {});
    return null;
  }
  const host = await mount(createElement(Host));
  const select = async (id) => {
    await act(async () => view.sessions.onSelect(id));
    await settle();
  };
  const action = () => view.messages.at(-1)?.actions?.[0];
  try {
    await select('demo');
    assert.equal(action()?.action.status, 'idle');
    await act(async () =>
      view.onMessageAction(action().messageId, action().action.key),
    );
    await settle();
    assert.equal(action()?.action.status, 'success');

    await select('other');
    await select('demo');
    assert.equal(action()?.action.status, 'success');
    assert.equal(lookups, 1);

    await select('other');
    replyId = 'answer-b';
    await select('demo');
    assert.equal(action()?.messageId, 'answer-b');
    assert.equal(action()?.action.status, 'success');
    assert.equal(lookups, 2);
  } finally {
    await host.unmount();
  }
});

test('a restored running commit only blocks submission of the reply it covers', async () => {
  let view;
  const submitted = [];
  const service = {
    listSessions: async () => [{ id: 'demo', title: 'Demo' }],
    loadMessages: async () => [
      {
        id: 'old-reply',
        role: 'assistant',
        parts: [{ type: 'text', text: 'Old reply' }],
      },
    ],
    async *stream() {
      yield { type: 'complete', text: 'New reply' };
    },
    getSessionCommitTask: async () => ({ id: 'task-a', status: 'running' }),
    async commitSession(id) {
      submitted.push(id);
      return { status: 'accepted', taskId: 'task-b' };
    },
  };
  const taskService = {
    getTask: async (id) => ({ id, status: 'running' }),
  };
  function Host() {
    view = useConversation({ chatOptions: { service, taskService } }, () => {});
    return null;
  }
  const host = await mount(createElement(Host));
  const action = () => view.messages.at(-1)?.actions?.[0];
  try {
    await act(async () => view.sessions.onSelect('demo'));
    await settle();
    assert.equal(action()?.messageId, 'old-reply');
    assert.equal(action()?.action.status, 'submitted');

    await act(async () =>
      view.onSend({ text: 'Please continue', attachments: [] }),
    );
    await settle();
    assert.equal(view.messages.at(-1)?.parts[0]?.text, 'New reply');
    assert.notEqual(action()?.messageId, 'old-reply');
    assert.equal(action()?.action.status, 'idle');
    await act(async () =>
      view.onMessageAction(action().messageId, action().action.key),
    );
    await settle();
    assert.deepEqual(submitted, ['demo']);
    assert.equal(action()?.action.status, 'submitted');
  } finally {
    await host.unmount();
  }
});
