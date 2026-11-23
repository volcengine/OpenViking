import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import {
  OpenVikingProvider,
  useConsole,
  useFileBrowser,
} from './helpers/internal-hooks.mjs';
import { mount, ok, recordingFetch } from './helpers/module-dom.mjs';

test('Provider language changes preserve Console records, errors, drafts and active requests', async () => {
  let state, complete, signal, pending;
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
  const element = (locale, local) =>
    h(
      OpenVikingProvider,
      { locale, baseURL: 'https://demo.invalid', fetch },
      h(Demo, { locale: local }),
    );
  const view = await mount(element('zh-CN'));
  try {
    await act(() => state.execute('ov read'));
    assert.match(state.error.message, /缺少参数 uri/);
    const id = state.records[0].id;
    await act(() => {
      state.setCommand('draft');
      state.setConversationDraft('Host 草稿');
    });
    await view.render(element('en-US'));
    assert.match(state.error.message, /Missing uri/);
    assert.equal(state.records[0].id, id);
    assert.equal(state.command, 'draft');
    assert.equal(state.conversationDraft, 'Host 草稿');
    await act(() => {
      pending = state.execute('ov health');
    });
    await view.render(element('zh-CN'));
    assert.equal(calls, 1);
    assert.equal(signal.aborted, false);
    assert.equal(state.isExecuting, true);
    await act(async () => {
      complete(Response.json({ status: 'ok', result: 'Server 原文' }));
      await pending;
    });
    assert.match(state.records[1].content.text, /Server 原文/);
    await view.render(element('en-US', 'zh-CN'));
    await act(() => state.execute('ov read'));
    assert.match(state.error.message, /缺少参数 uri/);
    await view.render(element('en-US'));
    assert.match(state.error.message, /Missing uri/);
    assert.equal(calls, 1);
  } finally {
    await view.unmount();
  }
});

test('Provider language changes reformat FileBrowser summaries without refetching', async () => {
  let state;
  const fetch = recordingFetch(({ path }) => {
    if (path === '/api/v1/fs/ls') return ok([]);
    if (path === '/api/v1/fs/stat') return ok({ isDir: true });
    if (path === '/api/v1/content/abstract')
      return Response.json(
        { status: 'error', error: { code: 'FAILED', message: '' } },
        { status: 503 },
      );
    return ok('Server overview 原文');
  });
  function Demo({ locale }) {
    state = useFileBrowser({
      rootUri: 'viking://',
      currentUri: 'viking://resources',
      controlled: true,
      locale,
      onUriChange() {},
    });
    return null;
  }
  const element = (locale, local) =>
    h(
      OpenVikingProvider,
      { locale, baseURL: 'https://demo.invalid', fetch },
      h(Demo, { locale: local }),
    );
  const view = await mount(element('zh-CN'));
  try {
    const count = fetch.calls.length;
    assert.equal(state.previewTabs[0].levels[0].label, 'L0 摘要');
    assert.match(state.previewTabs[0].levels[0].source.content, /摘要加载失败/);
    await view.render(element('en-US'));
    assert.equal(state.previewTabs[0].levels[0].label, 'L0 Summary');
    assert.match(
      state.previewTabs[0].levels[0].source.content,
      /The summary could not be loaded/,
    );
    assert.equal(
      state.previewTabs[0].levels[1].source.content,
      'Server overview 原文',
    );
    await view.render(element('en-US', 'zh-CN'));
    assert.equal(state.previewTabs[0].levels[0].label, 'L0 摘要');
    assert.equal(fetch.calls.length, count);
  } finally {
    await view.unmount();
  }
});
