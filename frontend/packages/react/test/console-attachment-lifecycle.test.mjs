import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createElement, StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { act } from './helpers/command-console-dom.mjs';
const hooks = process.env.OV_UI_TEST_HOOKS;
const { useConversation } = await import(
  pathToFileURL(join(hooks, 'console-conversation.js'))
);
const { OpenVikingProvider } = await import(
  pathToFileURL(join(hooks, 'provider.js'))
);
const tick = () => new Promise((resolve) => setImmediate(resolve));
const file = new File(['text'], 'notes.txt', { type: 'text/plain' });

async function mount(initial = {}) {
  let view;
  const requests = [];
  const sent = [];
  const service = {
    attachmentUpload: {
      upload: (file, signal) =>
        new Promise((resolve) => requests.push({ file, signal, resolve })),
    },
    createSession: async () => ({ id: 'created', title: 'Created' }),
    loadMessages: async () => [],
    listSessions: async () => [],
    removeSession: async () => {},
    async *stream(input) {
      sent.push(input);
      yield { type: 'complete', text: 'answer' };
    },
  };
  function Host({ props }) {
    view = useConversation(
      { ...props, chatOptions: { service, ...props.chatOptions } },
      () => {},
    );
    return null;
  }
  const root = createRoot(document.createElement('div'));
  const render = async ({ provider, ...props } = {}) =>
    act(async () => {
      root.render(
        createElement(
          StrictMode,
          null,
          createElement(
            OpenVikingProvider,
            provider,
            createElement(Host, { props }),
          ),
        ),
      );
      await tick();
    });
  await render(initial);
  return {
    requests,
    sent,
    service,
    render,
    get view() {
      return view;
    },
    add: () =>
      act(async () => {
        view.onFilesAdded([file]);
        await tick();
      }),
    finish: (index, id) =>
      act(async () => {
        requests[index].resolve(id);
        await tick();
      }),
    dispose: () => act(() => root.unmount()),
  };
}

for (const [name, first, second] of [
  [
    'Provider account',
    { provider: { accountId: 'a' } },
    { provider: { accountId: 'b' } },
  ],
  [
    'request identity',
    { requestOptions: { accountId: 'a' } },
    { requestOptions: { accountId: 'b' } },
  ],
  [
    'chat request identity',
    { chatOptions: { requestOptions: { userId: 'a' } } },
    { chatOptions: { requestOptions: { userId: 'b' } } },
  ],
  [
    'explicit session',
    { chatOptions: { sessionId: 'a' } },
    { chatOptions: { sessionId: 'b' } },
  ],
  [
    'host session',
    { conversation: { sessions: { currentId: 'a' } } },
    { conversation: { sessions: { currentId: 'b' } } },
  ],
  ['scope', { scopeKey: 'a' }, { scopeKey: 'b' }],
])
  test(`attachments cannot cross a ${name} change with a stable upload adapter`, async () => {
    const host = await mount(first);
    try {
      await host.add();
      await host.finish(0, 'old-ready');
      await host.add();
      await host.render(second);
      assert.equal(host.requests[1].signal.aborted, true);
      await host.finish(1, 'old-late');
      assert.deepEqual(host.view.attachments, []);
      await host.add();
      await host.finish(2, 'new-upload');
      await act(async () => {
        host.view.onSend({
          text: 'question',
          attachments: host.view.attachments,
        });
        await tick();
      });
      assert.deepEqual(
        host.sent[0].attachments.map((item) => item.uploadId),
        ['new-upload'],
      );
    } finally {
      await host.dispose();
    }
  });

for (const source of ['provider', 'requestOptions', 'chatOptions'])
  test(`aborting ${source} clears attachments and cancels pending upload`, async () => {
    const controller = new AbortController();
    const options =
      source === 'chatOptions'
        ? { chatOptions: { requestOptions: { signal: controller.signal } } }
        : { [source]: { signal: controller.signal } };
    const host = await mount(options);
    try {
      await host.add();
      await host.finish(0, 'ready-before-abort');
      await host.add();
      await act(() => controller.abort());
      assert.equal(host.requests[1].signal.aborted, true);
      await host.finish(1, 'late-after-abort');
      assert.deepEqual(host.view.attachments, []);
      await host.add();
      assert.equal(host.requests.length, 2);
      assert.deepEqual(host.view.attachments, []);
    } finally {
      await host.dispose();
    }
  });

test('newly created session ID echoed by the host retains its follow-up upload', async () => {
  const host = await mount();
  let finishCreation;
  host.service.createSession = () =>
    new Promise((resolve) => {
      finishCreation = resolve;
    });
  const sessions = {
    items: [],
    currentId: undefined,
    onSelect() {},
    onNew() {},
  };
  try {
    await host.render({ conversation: { sessions } });
    await act(async () => {
      host.view.onSend({ text: 'first question', attachments: [] });
      await tick();
    });
    await host.add();
    await act(async () => {
      finishCreation({ id: 'created', title: 'Created' });
      await tick();
    });
    assert.equal(host.view.sessions.currentId, 'created');
    await host.render({
      conversation: { sessions: { ...sessions, currentId: 'created' } },
    });
    assert.equal(host.requests[0].signal.aborted, false);
    await host.finish(0, 'follow-up-upload');
    assert.equal(host.view.attachments[0].uploadId, 'follow-up-upload');
  } finally {
    await host.dispose();
  }
});
