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
  const sessionUri = 'viking://user/demo/sessions/demo';
  let liveMessages = 0;
  let commitCount = 1;
  let taskStatus = 'running';
  let archiveStatus = 'completed';
  let archiveRequests = 0;
  let taskRequests = 0;
  const service = {
    listSessions: async () => [{ id: 'demo', title: 'Demo' }],
    loadMessages: async () => [
      {
        id: liveMessages ? 'new-answer' : 'saved-answer',
        role: 'assistant',
        parts: [{ type: 'text', text: 'Saved answer.' }],
      },
    ],
  };
  const fetch = async (url, { method }) => {
    const { pathname, searchParams } = new URL(url);
    assert.equal(method, 'GET');
    if (pathname === '/api/v1/sessions/demo') {
      return ok({
        uri: sessionUri,
        commit_count: commitCount,
        message_count: liveMessages,
        pending_tokens: 0,
      });
    }
    if (pathname === '/api/v1/tasks') {
      assert.equal(searchParams.get('resource_id'), 'demo');
      assert.equal(searchParams.get('task_type'), 'session_commit');
      assert.equal(searchParams.get('limit'), '1');
      if (!taskStatus) return ok([]);
      return ok([
        {
          task_id: 'memory-task',
          status: taskStatus,
          ...(taskStatus === 'completed' && {
            result: { archive_uri: `${sessionUri}/history/archive_001` },
          }),
        },
      ]);
    }
    if (pathname === '/api/v1/sessions/demo/archives/archive_002') {
      archiveRequests++;
      return ok({ archive_id: 'archive_002', status: archiveStatus });
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

    // A partial commit can leave a full turn live with zero pending tokens.
    liveMessages = 2;
    host = await mount(tree);
    await select();
    assert.equal(view.messages.at(-1)?.id, 'new-answer');
    assert.equal(status(), 'idle');
    await host.unmount();

    // A task from an older archive cannot certify the newest archive either.
    liveMessages = 0;
    commitCount = 2;
    host = await mount(tree);
    await select();
    assert.equal(status(), 'idle');
    await host.unmount();

    // The archive still provides durable completion after the task expires.
    taskStatus = undefined;
    host = await mount(tree);
    await select();
    assert.equal(status(), 'success');
    assert.equal(archiveRequests, 1);
    await host.unmount();

    archiveStatus = 'failed';
    host = await mount(tree);
    await select();
    assert.equal(status(), 'idle');
    await host.unmount();

    liveMessages = 2;
    archiveStatus = 'completed';
    host = await mount(tree);
    await select();
    assert.equal(status(), 'idle');
    assert.equal(archiveRequests, 2);
  } finally {
    await host.unmount();
  }
});

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
