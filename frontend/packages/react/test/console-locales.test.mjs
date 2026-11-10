import assert from 'node:assert/strict';
import { test } from 'node:test';
import { act, createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { useConsole } from '@openviking/react/modules/console';
import { mount } from './helpers/module-dom.mjs';

test('standalone Console Hook translates existing help and errors without changing data or drafts', async () => {
  let state;
  let calls = 0;
  const fetch = async () => {
    calls++;
    return Response.json(
      {
        status: 'error',
        error: { code: 'DENIED', message: 'Host reason 原文' },
      },
      { status: 403 },
    );
  };
  function Demo({ locale }) {
    state = useConsole({ locale });
    return null;
  }
  const element = (locale) =>
    h(
      OpenVikingProvider,
      { baseURL: 'https://demo.invalid', fetch },
      h(Demo, { locale }),
    );
  const view = await mount(element('zh-CN'));
  try {
    for (const command of ['ov help', 'ov unknown', 'ov read', 'ov health']) {
      await act(() => state.execute(command));
    }
    await act(() => {
      state.setCommand('ov read draft');
      state.setConversationDraft('Conversation 草稿');
    });
    assert.equal(state.records[0].content.sections[0].label, '可用命令');
    assert.match(state.records[1].content.text, /未知命令/);
    assert.match(state.records[2].content.text, /缺少参数 uri/);
    assert.equal(state.records[3].content.text, 'DENIED: Host reason 原文');
    const serverError = state.error;
    assert.equal(serverError.message, 'Host reason 原文');
    const ids = state.records.map((record) => record.id);
    await view.render(element('en-US'));
    assert.equal(
      state.records[0].content.sections[0].label,
      'Available commands',
    );
    assert.match(state.records[1].content.text, /Unknown command/);
    assert.match(state.records[2].content.text, /Missing uri/);
    assert.equal(state.records[3].content.text, 'DENIED: Host reason 原文');
    assert.equal(state.error, serverError);
    assert.equal(state.command, 'ov read draft');
    assert.equal(state.conversationDraft, 'Conversation 草稿');
    assert.deepEqual(
      state.records.map((record) => record.id),
      ids,
    );
    assert.ok(
      state.records.every((record) => !Object.hasOwn(record, 'localMessage')),
    );
    assert.equal(calls, 1);
  } finally {
    await view.unmount();
  }
});

test('standalone Console Hook localizes exposed generated errors without mutating previous errors', async () => {
  let state;
  function Demo({ locale }) {
    state = useConsole({ locale });
    return null;
  }
  const element = (locale) => h(Demo, { locale });
  const view = await mount(element('zh-CN'));
  try {
    await act(() => state.execute('ov read'));
    const chineseError = state.error;
    const id = state.records[0].id;
    assert.ok(chineseError instanceof Error);
    assert.match(chineseError.message, /缺少参数 uri/);
    assert.equal(chineseError.code, 'missingArgument');
    assert.equal(chineseError.value, 'uri');
    assert.match(chineseError.usage, /ov read <uri>/);
    await view.render(element('en-US'));
    const englishError = state.error;
    assert.match(englishError.message, /Missing uri/);
    assert.equal(englishError.code, chineseError.code);
    assert.equal(englishError.value, chineseError.value);
    assert.equal(englishError.usage, chineseError.usage);
    assert.match(state.records[0].content.text, /Missing uri/);
    assert.match(chineseError.message, /缺少参数 uri/);
    await view.render(element('en-US'));
    assert.equal(state.error, englishError);
    await view.render(element('zh-CN'));
    assert.match(state.error.message, /缺少参数 uri/);
    assert.match(state.records[0].content.text, /缺少参数 uri/);
    assert.match(englishError.message, /Missing uri/);
    assert.equal(state.records[0].id, id);
    assert.equal(state.failedCommand, 'ov read');
  } finally {
    await view.unmount();
  }
});

test('changing Hook locale keeps an active request and formats cancellation in the current language', async () => {
  let state, complete, pending, signal;
  let calls = 0;
  const fetch = (_url, init) => {
    calls++;
    signal = init.signal;
    return new Promise((resolve) => {
      complete = resolve;
    });
  };
  function Demo({ locale }) {
    state = useConsole({ locale });
    return null;
  }
  const element = (locale) =>
    h(
      OpenVikingProvider,
      { baseURL: 'https://demo.invalid', fetch },
      h(Demo, { locale }),
    );
  const view = await mount(element('en-US'));
  try {
    await act(() => {
      pending = state.execute('ov health');
    });
    await view.render(element('zh-CN'));
    assert.equal(calls, 1);
    assert.equal(signal.aborted, false);
    assert.equal(state.isExecuting, true);
    await act(() => state.cancel());
    assert.equal(signal.aborted, true);
    assert.match(state.records[0].content.text, /请求已取消/);
    await view.render(element('en-US'));
    assert.match(state.records[0].content.text, /Request cancelled/);
    await act(async () => {
      complete(Response.json({ status: 'ok', result: 'late response' }));
      await pending;
    });
    assert.match(state.records[0].content.text, /Request cancelled/);
  } finally {
    await view.unmount();
  }
});
