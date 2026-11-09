import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';

import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { mount, ok, recordingFetch } from './helpers/module-dom.mjs';

const ROOT = 'viking://';

test('a failed search shows the reason and a retry, not the empty copy', async () => {
  // 搜索失败时 `useFsSearch` 把结果置空并给出 error；只看 `searching`/`searchLoading`
  // 会把一次 403 渲染成「没有匹配结果」，用户既看不到原因也没有重试入口。
  //
  // A failed search clears the results and reports an error; reading only
  // `searching`/`searchLoading` renders a 403 as "no matching resources", with neither
  // the reason nor a retry.
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/search/find') {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            status: 'error',
            error: { code: 'FORBIDDEN', message: 'search forbidden' },
          }),
          { status: 403, headers: { 'content-type': 'application/json' } },
        ),
      );
    }
    if (call.path === '/api/v1/fs/ls') return ok([]);
    return ok('');
  });

  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(FileBrowser, null),
    ),
  );

  const input = tree.container.querySelector('input[type="search"]');
  assert.ok(input, 'the search box must exist');
  // 直接触发 change：React 的受控输入要经由原生 setter 才能被监听。
  // Fire change directly: React's controlled input needs the native setter to notice.
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value',
  ).set;
  setter.call(input, 'blocked');
  // 用 window 上的构造器：全局 `Event` 与 jsdom 的实例不互通。
  // Use the window's constructor: the global `Event` is not interchangeable with jsdom's.
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 50));

  const html = tree.container.innerHTML;
  // 结论必须是搜索专属文案，并带上服务端给的原因。
  // The conclusion must be search-specific copy and carry the server's reason.
  assert.match(html, /Failed to run the search/);
  assert.match(html, /search forbidden/);
  assert.match(html, /ov-file-browser__retry/);
  assert.doesNotMatch(html, /No matching resources/);
  await tree.unmount();
});
