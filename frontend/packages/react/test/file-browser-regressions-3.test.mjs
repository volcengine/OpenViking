import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement, Suspense } from 'react';

import { OpenVikingProvider } from './helpers/internal-hooks.mjs';
import { useFileBrowser } from './helpers/internal-hooks.mjs';
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

test('a suspended render keeps the committed tree working', async () => {
  // 这是本轮最容易写假的一条，因此写清它真正验证什么：
  //
  // `startTransition` 让 B 的渲染挂起并被 React 放弃，已提交的仍是 A。关键是**挂起
  // 点必须在 Hook 之后**：`useFileBrowser` 先执行，再抛出 promise。挂在 Hook 外层
  // （例如包一层 Gate）的话 React 根本不会进到 Hook，B 的作用域键从未被计算，这个
  // 竞态也就不存在——那样的测试会永远通过，证明不了任何事。
  //
  // 断言读 `seen.get()` 的最新快照，不能缓存某次渲染的 api 对象：Hook 每次渲染都返回
  // 新对象，缓存旧的那个会读到过期的 rows。
  //
  // This is the easiest test here to write falsely, so it states what it really checks:
  //
  // `startTransition` suspends B's render and React abandons it, leaving A committed. The
  // crucial part is that the **suspension point sits after the Hook**: `useFileBrowser`
  // runs first, then throws. Suspending outside the Hook (a wrapping Gate, say) means
  // React never enters the Hook at all, B's scope key is never computed, and the race
  // does not exist — such a test passes forever and proves nothing.
  //
  // Assertions read the latest snapshot from `seen.get()`; caching one render's api
  // object reports stale rows.
  const slow = deferred();
  const fetch = recordingFetch((call) => {
    if (call.path !== '/api/v1/fs/ls') return ok('');
    if (call.uri === ROOT) return ok([{ uri: DIR_A, isDir: true }]);
    return slow.promise;
  });

  const seen = new Map();
  const never = new Promise(() => {});
  function SuspendingProbe({ account, suspended }) {
    const api = useFileBrowser({
      rootUri: ROOT,
      currentUri: null,
      controlled: true,
      onUriChange() {},
    });
    seen.set(account, api);
    if (suspended) throw never;
    return createElement('i', null, account);
  }

  const mountTree = (account, suspended) =>
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch, accountId: account },
      createElement(
        Suspense,
        { fallback: createElement('b', null, 'fallback') },
        createElement(SuspendingProbe, { account, suspended }),
      ),
    );

  const tree = await mount(mountTree('A', false));
  const expanding = seen
    .get('A')
    .actions.toggleExpand(seen.get('A').rows.find((row) => row.uri === DIR_A));
  await new Promise((resolve) => setTimeout(resolve, 20));

  // 切到 B，那次渲染挂起并被放弃：已提交的仍是 A。
  // Switch to B; that render suspends and is abandoned, leaving A committed.
  await tree.renderTransition(mountTree('B', true));
  // B 的 Hook 确实执行过（否则这条测试不覆盖竞态），但从未提交。
  // B's Hook really ran — otherwise this test does not cover the race — yet never
  // committed.
  assert.ok(seen.has('B'), 'the suspended render must reach the Hook');
  assert.match(tree.container.innerHTML, /<i>A<\/i>/);

  // A 的响应在这期间回来，它属于仍然可见的 A。
  // A's response arrives meanwhile and belongs to the still-visible A.
  slow.resolve(ok([{ uri: `${DIR_A}/a.md` }]));
  await expanding;
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 40));
  });

  const uris = seen.get('A').rows.map((row) => row.uri);
  assert.ok(
    uris.includes(`${DIR_A}/a.md`),
    `A's children must land while B is suspended, got ${uris.join(',')}`,
  );
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
