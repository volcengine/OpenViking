import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';

import { OpenVikingProvider } from '@openviking/react/client';
import { useFileBrowser } from '@openviking/react/modules/file-browser';
import { scopeKeyOf } from '../src/modules/file-browser/fs-scope.ts';
import { deferred, mount, ok, recordingFetch } from './helpers/module-dom.mjs';

const ROOT = 'viking://';
const DIR_A = 'viking://a';
const FILE_A = 'viking://a.md';
const FILE_B = 'viking://b.md';

function probe(api) {
  return function Probe({ uri, controlled, options }) {
    api.current = useFileBrowser({
      rootUri: ROOT,
      currentUri: uri,
      controlled,
      requestOptions: options,
      onUriChange() {},
    });
    return null;
  };
}

test('the public currentUri follows the effective selection', async () => {
  // 非受控时宿主传的 `currentUri` 只是初值。若把它原样返回，消费 `currentUri` 的
  // 自定义视图会指向一个与预览不一致的资源。
  //
  // While uncontrolled the host's `currentUri` is only the initial value; returning it
  // as-is makes a custom view target a resource the preview no longer shows.
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') {
      return ok([{ uri: FILE_A }, { uri: FILE_B }]);
    }
    if (call.path === '/api/v1/content/read') return ok('# body\n');
    return ok('');
  });

  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: FILE_A, controlled: false }),
    ),
  );
  assert.equal(api.current.currentUri, FILE_A);

  await api.current.actions.select(
    api.current.rows.find((row) => row.uri === FILE_B),
  );
  await tree.render(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: FILE_A, controlled: false }),
    ),
  );

  assert.equal(api.current.currentUri, FILE_B);
  assert.equal(api.current.selectedId, FILE_B);
  await tree.unmount();
});

test('scope keys stay distinct when a value contains the separator', () => {
  // 拼接用的分隔符可能出现在字段值里；两个不同的身份组合会拼出同一个键，切换账号
  // 时旧请求就不会被判成过期。
  //
  // A joining separator can occur inside a field value; two different identity tuples
  // then produce the same key and a switch would not mark the old request stale.
  const first = scopeKeyOf(null, { accountId: 'A|value:B', userId: 'C' }, ROOT);
  const second = scopeKeyOf(
    null,
    { accountId: 'A', userId: 'B|value:C' },
    ROOT,
  );
  assert.notEqual(first, second);
});

test('a scope change does not discard the committed tree result', async () => {
  // 作用域身份只在提交后发布：切换账号的那次渲染若被放弃（`startTransition` +
  // Suspense 就会），已提交的 A 那棵树在途的响应仍必须被接受，否则目录会一直停在
  // 加载中。这里用「切到 B 但立刻切回 A」模拟同一个窗口：A 的响应不能被 B 那次
  // 渲染判成过期。
  //
  // The scope identity is published only after commit: if the render that switches the
  // account is abandoned (`startTransition` with Suspense does), the in-flight response
  // of the committed A tree must still be accepted, or the directory stays loading. A
  // switch to B followed immediately by a switch back to A stands in for that window:
  // A's response must not be judged stale by the B render.
  const slow = deferred();
  const fetch = recordingFetch((call) => {
    if (call.path !== '/api/v1/fs/ls') return ok('');
    if (call.uri === ROOT) return ok([{ uri: DIR_A, isDir: true }]);
    return slow.promise;
  });

  const api = { current: undefined };
  const Probe = probe(api);
  const mountTree = (account) =>
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch, accountId: account },
      createElement(Probe, { uri: null, controlled: true }),
    );

  const tree = await mount(mountTree('A'));
  const expanding = api.current.actions.toggleExpand(
    api.current.rows.find((row) => row.uri === DIR_A),
  );
  await new Promise((resolve) => setTimeout(resolve, 10));

  // A 的响应在切换前后回来，仍属于同一个已提交作用域。
  // A's response arrives around the switch and still belongs to the committed scope.
  slow.resolve(ok([{ uri: `${DIR_A}/a.md` }]));
  await expanding;
  await tree.render(mountTree('A'));

  const uris = api.current.rows.map((row) => row.uri);
  assert.ok(uris.includes(`${DIR_A}/a.md`), uris.join(','));
  await tree.unmount();
});

test('an empty failure message still marks the level as failed', async () => {
  // 服务端可以返回空字符串作为错误消息；用真假值判断会把一次失败重新变成空正文，
  // 于是「取不到」看起来像「还没生成」。
  //
  // A server may report an empty message; a truthiness check turns that failure back
  // into an empty body, so "could not load" looks like "not generated yet".
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') {
      return ok([{ uri: DIR_A, isDir: true }]);
    }
    if (call.path === '/api/v1/content/abstract') return ok('good L0');
    if (call.path === '/api/v1/content/overview') {
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
      createElement(Probe, { uri: DIR_A, controlled: true }),
    ),
  );

  const tab = api.current.previewTabs[0];
  assert.equal(tab.kind, 'directory');
  assert.equal(tab.levels[0].source.content, 'good L0');
  assert.match(tab.levels[1].source.content, /could not be loaded/i);
  await tree.unmount();
});
