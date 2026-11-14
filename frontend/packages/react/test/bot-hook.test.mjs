import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement, StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { OpenVikingProvider } from './helpers/internal-hooks.mjs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const { useVikingBotChat } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-chat-hook.js'))
);
import { act } from './helpers/command-console-dom.mjs';

test('internal chat survives StrictMode, uses Provider native auth and restores context; locale changes preserve content', async () => {
  let chat;
  const calls = [];
  const fetch = async (url, init) => {
    calls.push({ url, init });
    if (url.endsWith('/stream'))
      return new Response(
        'data: {"event":"reasoning_delta","data":"thought"}\n\ndata: {"event":"response","data":"answer"}\n\n',
        { headers: { 'Content-Type': 'text/event-stream' } },
      );
    const result = new URL(url, 'https://demo.invalid').pathname.endsWith(
      '/content/read',
    )
      ? JSON.stringify({
          id: 'past',
          role: 'user',
          parts: [{ type: 'text', text: 'original' }],
        })
      : {
          session_id: 's1',
          commit_count: 0,
          uri: 'viking://user/alice/sessions/past-session',
        };
    return Response.json({ status: 'ok', result });
  };
  function Host() {
    chat = useVikingBotChat();
    return null;
  }
  const container = document.createElement('div');
  const root = createRoot(container);
  const render = (locale) =>
    act(() =>
      root.render(
        createElement(
          StrictMode,
          null,
          createElement(
            OpenVikingProvider,
            { fetch, locale, apiKey: 'test-only', userId: 'alice' },
            createElement(Host),
          ),
        ),
      ),
    );
  try {
    await render('zh-CN');
    await act(() => chat.send('question'));
    assert.equal(chat.messages.at(-1).parts[0].text, 'thought');
    assert.equal(chat.messages.at(-1).parts[1].text, 'answer');
    const requests = calls.length;
    await render('en-US');
    assert.equal(calls.length, requests);
    assert.equal(chat.messages.at(-1).parts[1].text, 'answer');
    assert.equal(calls[0].init.headers['x-api-key'], 'test-only');
    assert.equal(calls[0].init.headers['x-openviking-user'], 'alice');
    await act(() => chat.selectSession('past-session'));
    assert.equal(chat.messages[0].parts[0].text, 'original');
    assert.ok(
      calls.some(({ url }) =>
        new URL(url, 'https://demo.invalid').searchParams
          .get('uri')
          ?.endsWith('/past-session/messages.jsonl'),
      ),
    );
    assert.ok(
      !calls.some(
        ({ url, init }) => url.endsWith('/messages') && init.method === 'GET',
      ),
    );
  } finally {
    await act(() => root.unmount());
  }
});

test('client failure notices follow the display locale without restarting requests and disappear on scope change', async () => {
  let chat;
  let creations = 0;
  let streams = 0;
  const service = {
    createSession: async () => ({ id: `s${++creations}`, title: 'Session' }),
    loadMessages: async () => [],
    async *stream() {
      streams++;
      yield { type: 'text', text: 'unfinished content', delta: true };
    },
  };
  function Host({ locale, scopeKey }) {
    chat = useVikingBotChat({ service, locale, scopeKey });
    return null;
  }
  const root = createRoot(document.createElement('div'));
  const render = (locale, scopeKey = 'first') =>
    act(() => root.render(createElement(Host, { locale, scopeKey })));
  try {
    await render('zh-CN');
    await act(() => chat.send('original question'));
    const notice = chat.messages.at(-1);
    assert.equal(notice.role, 'system');
    assert.equal(notice.tone, 'error');
    assert.equal(notice.clientError.code, 'BOT_STREAM_TRUNCATED');
    assert.match(notice.parts[0].text, /完成前断开/);
    assert.equal(notice.clientError, chat.error);
    await render('en-US');
    assert.equal(chat.messages.at(-1).id, notice.id);
    assert.match(chat.messages.at(-1).parts[0].text, /terminal event/);
    assert.equal(chat.messages[0].parts[0].text, 'original question');
    assert.equal(chat.messages[1].parts[0].text, 'unfinished content');
    assert.equal(creations, 1);
    assert.equal(streams, 1);
    await render('en-US', 'second');
    assert.deepEqual(chat.messages, []);
    assert.equal(chat.error, undefined);
  } finally {
    await act(() => root.unmount());
  }
});
