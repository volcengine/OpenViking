import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import {
  useFileBrowser,
  OpenVikingProvider as HookProvider,
} from './helpers/internal-hooks.mjs';
import { mount, ok, recordingFetch } from './helpers/module-dom.mjs';

test('file-browser failure and retry copy update with locale while keeping the server reason', async () => {
  const fetch = () =>
    Promise.resolve(
      Response.json(
        {
          status: 'error',
          error: { code: 'FORBIDDEN', message: 'Host reason' },
        },
        { status: 403 },
      ),
    );
  const element = (locale) =>
    h(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      h(FileBrowser, { locale, defaultUri: 'viking://resources/demo.md' }),
    );
  const view = await mount(element('zh-CN'));
  assert.match(view.container.textContent, /Host reason/);
  assert.match(view.container.textContent, /重试/);
  assert.doesNotMatch(view.container.textContent, /Retry/);
  await view.render(element('en-US'));
  assert.match(view.container.textContent, /Host reason/);
  assert.match(view.container.textContent, /Retry/);
  await view.unmount();
});

test('file-browser preview footer follows locale and supports a typed host formatter', async () => {
  const fetch = recordingFetch(({ path }) => {
    if (path === '/api/v1/fs/ls') return ok([]);
    if (path === '/api/v1/fs/stat') return ok({ isDir: false });
    return ok('');
  });
  const element = (locale, labels) =>
    h(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      h(FileBrowser, {
        locale,
        labels,
        defaultUri: 'viking://resources/empty.txt',
      }),
    );
  const view = await mount(element('zh-CN'));
  assert.match(view.container.textContent, /0 字符数/);
  await view.render(element('en-US'));
  assert.match(view.container.textContent, /0 characters/);
  await view.render(
    element('zh-CN', {
      formatCharacterCount: (count) => `Host count=${count}`,
    }),
  );
  assert.match(view.container.textContent, /Host count=0/);
  await view.unmount();
});

test('standalone file-browser Hook localizes loaded summaries without refetching', async () => {
  let state;
  const fetch = recordingFetch(({ path }) => {
    if (path === '/api/v1/fs/ls') return ok([]);
    if (path === '/api/v1/fs/stat') return ok({ isDir: true });
    if (path === '/api/v1/content/abstract') {
      return Response.json(
        { status: 'error', error: { code: 'FAILED', message: '' } },
        { status: 503 },
      );
    }
    return ok('Server overview 原文');
  });
  function Demo({ locale, summaryLabels }) {
    state = useFileBrowser({
      rootUri: 'viking://',
      currentUri: 'viking://resources',
      controlled: true,
      locale,
      summaryLabels,
      onUriChange() {},
    });
    return null;
  }
  const element = (locale, summaryLabels) =>
    h(
      HookProvider,
      { baseURL: 'https://ov.test', fetch },
      h(Demo, { locale, summaryLabels }),
    );
  const view = await mount(element('zh-CN'));
  try {
    const calls = fetch.calls.length;
    assert.equal(state.previewTabs[0].levels[0].label, 'L0 摘要');
    assert.match(state.previewTabs[0].levels[0].source.content, /摘要加载失败/);
    assert.equal(
      state.previewTabs[0].levels[1].source.content,
      'Server overview 原文',
    );
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
    assert.equal(fetch.calls.length, calls);
    await view.render(
      element('zh-CN', {
        l0: 'Host L0',
        l1: 'Host L1',
        failure: 'Host fallback',
      }),
    );
    assert.equal(state.previewTabs[0].levels[0].label, 'Host L0');
    assert.equal(
      state.previewTabs[0].levels[0].source.content,
      '> Host fallback',
    );
    assert.equal(fetch.calls.length, calls);
  } finally {
    await view.unmount();
  }
});
