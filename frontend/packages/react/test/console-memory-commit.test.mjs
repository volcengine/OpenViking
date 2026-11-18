import assert from 'node:assert/strict';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { act, createElement, StrictMode } from 'react';
import { mount, ok, settle } from './helpers/module-dom.mjs';

const bundled = (name) =>
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, name));
const { useConversation } = await import(bundled('console-conversation.js'));
const { OpenVikingProvider } = await import(bundled('provider.js'));

test('a managed conversation waits for message writes before submitting to the provider and tracking memory extraction', async () => {
  const requests = [];
  const saved = [];
  let savedAssistant;
  const persistence = new Promise((resolve) => {
    savedAssistant = resolve;
  });
  let finished;
  const extraction = new Promise((resolve) => {
    finished = resolve;
  });
  const fetch = async (url, init) => {
    const path = new URL(url).pathname;
    requests.push({ path, method: init.method, body: init.body });
    if (path.endsWith('/commit'))
      return ok({ status: 'accepted', task_id: 'memory-task' });
    if (path.endsWith('/tasks/memory-task')) return extraction;
    throw new Error(`Unexpected request: ${path}`);
  };
  const service = {
    createSession: async () => ({ id: 'demo', title: 'Demo' }),
    listSessions: async () => [],
    loadMessages: async () => [],
    async removeSession() {},
    async *stream() {
      yield { type: 'complete', text: 'I can remember this.' };
    },
    async saveMessage(_id, message) {
      if (message.role === 'assistant') await persistence;
      saved.push(message.role);
    },
  };
  let view;
  function Host() {
    view = useConversation({ chatOptions: { service } }, () => {});
    return null;
  }
  const host = await mount(
    createElement(
      StrictMode,
      null,
      createElement(
        OpenVikingProvider,
        { fetch, baseURL: 'https://ov.test' },
        createElement(Host),
      ),
    ),
  );
  const lastAction = () => view.messages.at(-1)?.actions?.[0];
  const tick = () => new Promise((resolve) => setTimeout(resolve, 10));
  try {
    await act(() =>
      view.onSend({ text: 'Please remember this.', attachments: [] }),
    );
    await settle();
    const { messageId, action } = lastAction();
    assert.equal(action.status, 'idle');
    await act(() => {
      view.onMessageAction(messageId, action.key);
      view.onMessageAction(messageId, action.key);
    });
    assert.equal(lastAction().action.status, 'pending');
    assert.deepEqual(saved, ['user']);
    assert.deepEqual(requests, []);
    await act(async () => {
      savedAssistant();
      await tick();
    });
    assert.deepEqual(saved, ['user', 'assistant']);
    assert.equal(lastAction().action.status, 'submitted');
    assert.deepEqual(
      requests.map(({ path, method }) => [path, method]),
      [
        ['/api/v1/sessions/demo/commit', 'POST'],
        ['/api/v1/tasks/memory-task', 'GET'],
      ],
    );
    assert.deepEqual(JSON.parse(requests[0].body), { keep_recent_count: 0 });
    await act(async () => {
      finished(ok({ task_id: 'memory-task', status: 'completed' }));
      await tick();
    });
    assert.equal(lastAction().action.status, 'success');
  } finally {
    await host.unmount();
  }
});
