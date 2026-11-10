import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';

import { OpenVikingProvider } from '@openviking/react/client';
import { useFileBrowser } from '@openviking/react/modules/file-browser';
import { mount, ok, recordingFetch } from './helpers/module-dom.mjs';

const ROOT = 'viking://';
const DIR = 'viking://dir';

/**
 * 宿主传入的中文摘要文案。一个真实的宿主会把它们映射到自己的 i18n。
 * Chinese summary copy a host would supply; a real host maps these to its own i18n.
 */
const zhSummary = {
  l0: '摘要',
  l1: '总览',
  failure: '摘要加载失败',
};

function probe(api) {
  return function Probe() {
    api.current = useFileBrowser({
      rootUri: ROOT,
      currentUri: DIR,
      controlled: true,
      summaryLabels: zhSummary,
      onUriChange() {},
    });
    return null;
  };
}

test('the summary failure fallback uses the host’s label', async () => {
  // 兜底文案必须来自可替换文案：写死英文会让中文界面里冒出一句英文。
  // The fallback must come from replaceable copy: a hard-coded English line would leak
  // into a Chinese interface.
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') return ok([{ uri: DIR, isDir: true }]);
    if (call.path === '/api/v1/content/abstract') return ok('好的 L0');
    if (call.path === '/api/v1/content/overview') {
      // 服务端返回空消息，正是触发兜底的那一种失败。
      // The server reports an empty message, the failure that triggers the fallback.
      return Promise.resolve(
        new Response(
          JSON.stringify({
            status: 'error',
            error: { code: 'INTERNAL', message: '' },
          }),
          { status: 500, headers: { 'content-type': 'application/json' } },
        ),
      );
    }
    return ok('');
  });

  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, null),
    ),
  );

  const tab = api.current.previewTabs[0];
  assert.equal(tab.kind, 'directory');
  assert.equal(tab.levels[0].label, '摘要');
  assert.equal(tab.levels[0].source.content, '好的 L0');
  // 失败的那一级用宿主给的兜底文案，不是英文。
  // The failed level uses the host's fallback copy rather than English.
  assert.match(tab.levels[1].source.content, /摘要加载失败/);
  assert.doesNotMatch(tab.levels[1].source.content, /could not be loaded/);
  await tree.unmount();
});

test('a non-empty server message still wins over the fallback', async () => {
  // 有具体原因时显示原因，兜底只在服务端没给消息时使用。
  // A concrete reason is shown as-is; the fallback only applies when the server gave
  // no message.
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') return ok([{ uri: DIR, isDir: true }]);
    if (call.path === '/api/v1/content/abstract') return ok('好的 L0');
    if (call.path === '/api/v1/content/overview') {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            status: 'error',
            error: { code: 'FORBIDDEN', message: '权限不足' },
          }),
          { status: 403, headers: { 'content-type': 'application/json' } },
        ),
      );
    }
    return ok('');
  });

  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, null),
    ),
  );

  assert.match(api.current.previewTabs[0].levels[1].source.content, /权限不足/);
  await tree.unmount();
});
