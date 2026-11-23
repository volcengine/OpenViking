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
    archiveStatus = 'pending';
    host = await mount(tree);
    await select();
    assert.equal(status(), 'idle');
    await host.unmount();

    // The archive still provides durable completion after the task expires.
    archiveStatus = 'completed';
    for (const remainingTask of [
      undefined,
      'failed',
      'cancelled',
      'completed',
    ]) {
      taskStatus = remainingTask;
      host = await mount(tree);
      await select();
      assert.equal(status(), 'success', String(remainingTask));
      await host.unmount();
    }

    taskStatus = undefined;
    archiveStatus = 'failed';
    host = await mount(tree);
    await select();
    assert.equal(status(), 'idle');
    await host.unmount();

    const lookupsBeforeLiveMessages = archiveRequests;
    liveMessages = 2;
    archiveStatus = 'completed';
    host = await mount(tree);
    await select();
    assert.equal(status(), 'idle');
    assert.equal(archiveRequests, lookupsBeforeLiveMessages);
  } finally {
    await host.unmount();
  }
});
