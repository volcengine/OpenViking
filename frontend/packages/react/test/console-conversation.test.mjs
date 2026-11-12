import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { act } from './helpers/command-console-dom.mjs';
const { useConversation } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'console-conversation.js'))
);
const tick = () => new Promise((resolve) => setImmediate(resolve));

test('Console delegates native ownership to the headless hook, preserves failed draft and omits unsupported stop', async () => {
  let view;
  let draft = 'question';
  let fail = true;
  let lists = 0;
  const service = {
    createSession: async () => ({ id: 'session', title: 'session' }),
    loadMessages: async () => [],
    listSessions: async () => {
      lists++;
      return [];
    },
    removeSession: async () => {},
    async *stream() {
      if (fail) throw new Error('backend failure');
      yield { type: 'complete', text: 'answer' };
    },
  };
  const root = createRoot(document.createElement('div'));
  function Host({ controlled }) {
    view = useConversation(
      {
        chatOptions: { service },
        conversation: controlled ? { messages: [] } : undefined,
      },
      (value) => {
        draft = typeof value === 'function' ? value(draft) : value;
      },
    );
    return null;
  }
  try {
    await act(() => root.render(createElement(Host, { controlled: true })));
    assert.equal(lists, 0);
    await act(() => root.render(createElement(Host, { controlled: false })));
    assert.equal(view.onCancel, undefined);
    await act(async () => {
      view.onSend({ text: 'question', attachments: [] });
      await tick();
    });
    assert.equal(draft, 'question');
    assert.equal(view.isSending, false);
    assert.equal(view.messages[0].parts[0].text, 'question');
    fail = false;
    await act(async () => {
      view.onSend({ text: 'question', attachments: [] });
      await tick();
    });
    assert.equal(draft, '');
    assert.equal(view.messages.at(-1).parts[0].text, 'answer');
  } finally {
    await act(() => root.unmount());
  }
});

test('native Console accepts follow-ups while streaming and preserves newer drafts across acceptance and completion', async () => {
  let view;
  let draft = 'question';
  let advance;
  let accept;
  const streamGate = new Promise((resolve) => {
    advance = resolve;
  });
  const ackGate = new Promise((resolve) => {
    accept = resolve;
  });
  let reject = true;
  const service = {
    createSession: async () => ({ id: 's', title: 's' }),
    loadMessages: async () => [],
    listSessions: async () => [],
    removeSession: async () => {},
    steer: async () => (reject ? { accepted: false } : ackGate),
    async *stream() {
      yield { type: 'reasoning', text: 'before', delta: true };
      await streamGate;
      yield { type: 'iteration', iteration: 2 };
      yield { type: 'complete', text: 'answer' };
    },
  };
  function Host() {
    view = useConversation({ chatOptions: { service } }, (next) => {
      draft = typeof next === 'function' ? next(draft) : next;
    });
    return null;
  }
  const root = createRoot(document.createElement('div'));
  try {
    await act(() => root.render(createElement(Host)));
    await act(async () => {
      view.onSend({ text: 'question', attachments: [] });
      await tick();
    });
    assert.equal(draft, '');
    assert.equal(view.canSendWhileStreaming, true);
    draft = 'follow';
    await act(async () => {
      view.onSend({ text: 'follow', attachments: [] });
      await tick();
    });
    assert.equal(draft, 'follow');
    assert.equal(
      view.messages.filter((item) => item.role === 'user').length,
      1,
    );
    reject = false;
    await act(() => {
      view.onSend({ text: 'follow', attachments: [] });
    });
    assert.equal(view.isSubmitting, true);
    draft = 'new draft';
    await act(async () => {
      accept({ accepted: true });
      await tick();
    });
    assert.equal(view.isSubmitting, false);
    assert.equal(draft, 'new draft');
    await act(async () => {
      advance();
      await tick();
    });
    assert.equal(view.isSending, false);
    assert.equal(draft, 'new draft');
    assert.deepEqual(
      view.messages.map((item) => item.role),
      ['user', 'assistant', 'user', 'assistant'],
    );
  } finally {
    await act(() => root.unmount());
  }
});
