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

test('managed attachments upload, reach chat and discard cancelled uploads', async () => {
  const requests = [];
  const sent = [];
  const service = {
    attachmentUpload: {
      accept: '.txt',
      upload: (file, signal) =>
        new Promise((resolve, reject) =>
          requests.push({ file, signal, resolve, reject }),
        ),
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
  let view;
  function Host() {
    view = useConversation({ chatOptions: { service } }, () => {});
    return null;
  }
  const root = createRoot(document.createElement('div'));
  const file = new File(['text'], 'notes.txt', { type: 'text/plain' });
  const add = () =>
    act(async () => {
      view.onFilesAdded([file, file]);
      await tick();
    });
  try {
    await act(() =>
      root.render(createElement(StrictMode, null, createElement(Host))),
    );
    assert.equal(view.accept, '.txt');
    await add();
    assert.deepEqual(
      view.attachments.map(({ status }) => status),
      ['uploading', 'uploading'],
    );
    await act(async () => {
      requests[0].resolve('temp-id');
      requests[1].reject(new Error('upload rejected'));
      await tick();
    });
    assert.equal(view.attachments[0].uploadId, 'temp-id');
    assert.equal(view.attachments[1].errorMessage, 'upload rejected');
    await act(() => view.onAttachmentRemove(view.attachments[1]));
    await act(async () => {
      view.onSend({ text: '', attachments: view.attachments });
      await tick();
    });
    assert.equal(sent[0].attachments[0].uploadId, 'temp-id');
    assert.equal(view.attachments.length, 0);
    await add();
    await act(() => view.onAttachmentRemove(view.attachments[0]));
    assert.equal(requests[2].signal.aborted, true);
    await act(() => view.sessions.onNew());
    assert.equal(requests[3].signal.aborted, true);
    await act(async () => {
      requests[2].resolve('discarded');
      requests[3].resolve('discarded');
      await tick();
    });
    assert.equal(view.attachments.length, 0);
  } finally {
    await act(() => root.unmount());
  }
});
