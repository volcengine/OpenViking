import assert from 'node:assert/strict';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { act, createElement } from 'react';
import { mount, ok, settle } from './helpers/module-dom.mjs';

const bundled = (name) =>
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, name));
const { useConversation } = await import(bundled('console-conversation.js'));
const { OpenVikingProvider } = await import(bundled('provider.js'));

for (const persistsFlag of [true, false]) {
  test(`interrupted native history stays ineligible ${persistsFlag ? 'across mounts' : 'with a legacy server in the same scope'}`, async () => {
    const id = 'stored-session';
    const stored = [];
    const writes = [];
    let view;
    let first = true;
    const fetch = async (url, init) => {
      const { pathname } = new URL(url);
      if (pathname === '/api/v1/sessions')
        return ok(
          init.method === 'POST' ? { session_id: id } : [{ session_id: id }],
        );
      if (pathname === `/api/v1/sessions/${id}`)
        return ok({
          commit_count: 0,
          uri: `viking://user/demo/sessions/${id}`,
        });
      if (pathname === `/api/v1/sessions/${id}/messages`) {
        const message = JSON.parse(init.body);
        writes.push(message);
        const item = { id: `msg-${stored.length}`, ...message };
        if (!persistsFlag) delete item.interrupted;
        stored.push(item);
        return ok({});
      }
      if (pathname === '/api/v1/tasks') return ok([]);
      if (pathname === '/api/v1/content/read')
        return ok(stored.map((message) => JSON.stringify(message)).join('\n'));
      if (pathname === '/bot/v1/chat/stream') {
        const events = first
          ? [
              { event: 'content_delta', data: 'Partial answer.' },
              { event: 'interrupt' },
            ]
          : [{ event: 'response', data: 'Complete answer.' }];
        first = false;
        return new Response(
          events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
          {
            headers: { 'content-type': 'text/event-stream' },
          },
        );
      }
      throw new Error(`Unexpected request: ${pathname}`);
    };
    function Host() {
      view = useConversation({}, () => {});
      return null;
    }
    const tree = createElement(
      OpenVikingProvider,
      { fetch, baseURL: 'https://ov.test' },
      createElement(Host),
    );
    let host = await mount(tree);
    const send = async (text) => {
      await act(async () => view.onSend({ text, attachments: [] }));
      await settle();
    };
    const reopen = async () => {
      await act(async () => view.sessions.onSelect(id));
      await settle();
    };
    try {
      await send('First question');
      assert.equal(view.messages.at(-1)?.interrupted, true);
      if (persistsFlag) {
        await host.unmount();
        host = await mount(tree);
      } else {
        await act(async () => view.sessions.onNew());
      }
      await reopen();
      assert.equal(writes[1]?.interrupted, true);
      assert.equal(view.messages.at(-1)?.id, 'msg-1');
      assert.equal(view.messages.at(-1)?.parts[0]?.text, 'Partial answer.');
      assert.equal(view.messages.at(-1)?.actions, undefined);

      await send('Next question');
      await reopen();
      assert.equal(writes[3]?.interrupted, undefined);
      assert.equal(view.messages.at(-1)?.parts[0]?.text, 'Complete answer.');
      assert.equal(
        view.messages.at(-1)?.actions?.[0]?.action.label,
        'Commit session',
      );
    } finally {
      await host.unmount();
    }
  });
}

test('restores a submitted session on remount and allows submission after new messages', async () => {
  let view;
  let pendingTokens = 0;
  let taskStatus = 'running';
  let taskRequests = 0;
  const service = {
    listSessions: async () => [{ id: 'demo', title: 'Demo' }],
    loadMessages: async () => [
      {
        id: pendingTokens ? 'new-answer' : 'saved-answer',
        role: 'assistant',
        parts: [{ type: 'text', text: 'Saved answer.' }],
      },
    ],
  };
  const fetch = async (url, { method }) => {
    const { pathname, searchParams } = new URL(url);
    assert.equal(method, 'GET');
    if (pathname === '/api/v1/sessions/demo') {
      return ok({ pending_tokens: pendingTokens });
    }
    if (pathname === '/api/v1/tasks') {
      assert.equal(searchParams.get('resource_id'), 'demo');
      assert.equal(searchParams.get('task_type'), 'session_commit');
      assert.equal(searchParams.get('limit'), '1');
      return ok([{ task_id: 'memory-task', status: taskStatus }]);
    }
    if (pathname === '/api/v1/tasks/memory-task') {
      taskRequests++;
      return ok({ task_id: 'memory-task', status: taskStatus });
    }
    throw new Error(`Unexpected request: ${pathname}`);
  };
  function Host() {
    view = useConversation({ chatOptions: { service } }, () => {});
    return null;
  }
  const tree = createElement(
    OpenVikingProvider,
    { fetch, baseURL: 'https://ov.test' },
    createElement(Host),
  );
  const select = async () => {
    await act(async () => view.sessions.onSelect('demo'));
    await settle();
  };
  const status = () => view.messages.at(-1)?.actions?.[0]?.action.status;
  let host = await mount(tree);
  try {
    await select();
    assert.equal(status(), 'submitted');
    assert.equal(taskRequests, 1);
    await host.unmount();

    taskStatus = 'completed';
    host = await mount(tree);
    await select();
    assert.equal(status(), 'success');
    assert.equal(taskRequests, 1);
    await host.unmount();

    pendingTokens = 12;
    host = await mount(tree);
    await select();
    assert.equal(view.messages.at(-1)?.id, 'new-answer');
    assert.equal(status(), 'idle');
  } finally {
    await host.unmount();
  }
});
