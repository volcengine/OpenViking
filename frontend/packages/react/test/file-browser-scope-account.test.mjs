import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement, StrictMode } from 'react';

import { OpenVikingProvider } from './helpers/internal-hooks.mjs';
import { useFileBrowser } from './helpers/internal-hooks.mjs';
import { deferred, mount, ok, recordingFetch } from './helpers/module-dom.mjs';

const ROOT = 'viking://';
const DIR_A = 'viking://a';

function probe(api) {
  return function Probe({ uri, options }) {
    api.current = useFileBrowser({
      rootUri: ROOT,
      currentUri: uri,
      requestOptions: options,
      onUriChange() {},
    });
    return null;
  };
}

const listing = (entries) => ok(entries);

test('an expansion still works after Strict Mode replays the effect', async () => {
  // Strict Mode 会执行 setup → cleanup → setup。若清理把作用域控制器中止后不再
  // 替换，第二次 setup 之后每次操作都在入口处直接返回，树再也不发请求。
  //
  // Strict Mode runs setup → cleanup → setup. If cleanup aborts the scope controller
  // without replacing it, every operation returns at its entry after the second
  // setup and the tree never issues another request.
  const fetch = recordingFetch((call) => {
    if (call.path !== '/api/v1/fs/ls') return ok({});
    if (call.uri === DIR_A) return listing([{ uri: `${DIR_A}/child.md` }]);
    return listing([{ uri: DIR_A, isDir: true }]);
  });

  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(
      StrictMode,
      null,
      createElement(
        OpenVikingProvider,
        { baseURL: 'https://ov.test', fetch },
        createElement(Probe, { uri: null }),
      ),
    ),
  );

  const before = fetch.countOf('/api/v1/fs/ls');
  const dirRow = api.current.rows.find((row) => row.uri === DIR_A);
  await api.current.actions.select(dirRow);
  await tree.render(
    createElement(
      StrictMode,
      null,
      createElement(
        OpenVikingProvider,
        { baseURL: 'https://ov.test', fetch },
        createElement(Probe, { uri: null }),
      ),
    ),
  );

  assert.ok(
    fetch.countOf('/api/v1/fs/ls') > before,
    'expanding a directory must issue a request after the Strict Mode replay',
  );
  assert.ok(
    api.current.rows.some((row) => row.uri === `${DIR_A}/child.md`),
    'the children must land in the tree',
  );

  // 刷新同样要能发出请求。
  // Refresh must issue requests too.
  const beforeRefresh = fetch.countOf('/api/v1/fs/ls');
  await api.current.actions.refresh();
  await tree.render(
    createElement(
      StrictMode,
      null,
      createElement(
        OpenVikingProvider,
        { baseURL: 'https://ov.test', fetch },
        createElement(Probe, { uri: null }),
      ),
    ),
  );
  assert.ok(fetch.countOf('/api/v1/fs/ls') > beforeRefresh);
  await tree.unmount();
});

test('a per-request accountId change invalidates the pending expansion', async () => {
  // 同一个 Provider 下把 requestOptions.accountId 从 A 换成 B，客户端对象不变但
  // 数据空间换了；A 在途的响应不能落到 B 的树上。
  //
  // Switching requestOptions.accountId from A to B under the same Provider leaves the
  // client object unchanged while the data space changes; A's in-flight response must
  // not land on B's tree.
  const slow = deferred();
  const fetch = recordingFetch((call) => {
    if (call.path !== '/api/v1/fs/ls') return ok({});
    // 根目录先给出 DIR_A，才能点它展开；展开请求在账号 A 下挂住。
    // The root lists DIR_A so it can be expanded; the expansion hangs under account A.
    if (call.uri === ROOT) return listing([{ uri: DIR_A, isDir: true }]);
    if (call.init?.headers?.['x-openviking-account'] === 'A') {
      return slow.promise;
    }
    return listing([{ uri: `${DIR_A}/from-b.md` }]);
  });

  const api = { current: undefined };
  const Probe = probe(api);
  // options 要按账号缓存：每次渲染都新建对象会让树反复重新加载，行被清空。
  // Cache the options per account: a fresh object every render makes the tree reload
  // repeatedly and its rows disappear.
  const optionsByAccount = {
    A: { accountId: 'A' },
    B: { accountId: 'B' },
  };
  const mountTree = (account) =>
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: null, options: optionsByAccount[account] }),
    );

  const tree = await mount(mountTree('A'));
  // `select` 会先选中再展开，展开请求就是这里要挂住的那一个。
  // `select` commits the selection and then expands, so this is the request to hang.
  void api.current.actions.select(
    api.current.rows.find((row) => row.uri === DIR_A),
  );
  await tree.render(mountTree('B'));
  slow.resolve(listing([{ uri: `${DIR_A}/from-a.md` }]));
  await tree.render(mountTree('B'));

  const uris = api.current.rows.map((row) => row.uri);
  assert.ok(!uris.includes(`${DIR_A}/from-a.md`), uris.join(','));
  await tree.unmount();
});

test('a refresh stops when the account changes mid-loop', async () => {
  // 刷新是多轮请求的循环：中途换账号就该停下，而不是用旧客户端继续往新账号的
  // 树上写。
  //
  // Refresh loops over several requests: an account switch mid-loop must stop it
  // rather than keep writing to the new account's tree with the old client.
  const slowRoot = deferred();
  // 只挂住刷新那一次的根目录请求：首次加载必须正常返回，否则连目录行都没有。
  // Only the refresh's root request hangs: the first load must return, or there is no
  // directory row to expand at all.
  let rootCalls = 0;
  let hangFrom = Number.POSITIVE_INFINITY;
  const fetch = recordingFetch((call) => {
    if (call.path !== '/api/v1/fs/ls') return ok({});
    if (call.uri === DIR_A) return listing([{ uri: `${DIR_A}/from-a.md` }]);
    rootCalls += 1;
    if (rootCalls >= hangFrom) return slowRoot.promise;
    return listing([{ uri: DIR_A, isDir: true }]);
  });

  const api = { current: undefined };
  const Probe = probe(api);
  const optionsByAccount = {
    A: { accountId: 'A' },
    B: { accountId: 'B' },
  };
  const mountTree = (account) =>
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: null, options: optionsByAccount[account] }),
    );

  const tree = await mount(mountTree('A'));
  // 先展开一次，让 DIR_A 进入展开集合，刷新才有第二轮请求。
  // Expand once so DIR_A is in the expanded set and refresh has a second iteration.
  const dirRow = api.current.rows.find((row) => row.uri === DIR_A);
  assert.ok(dirRow, 'the root listing must contain the directory');
  await api.current.actions.toggleExpand(dirRow);
  await tree.render(mountTree('A'));

  hangFrom = rootCalls + 1;
  const refreshing = api.current.actions.refresh();
  await tree.render(mountTree('B'));
  slowRoot.resolve(listing([{ uri: DIR_A, isDir: true }]));
  await refreshing;
  await tree.render(mountTree('B'));

  const uris = api.current.rows.map((row) => row.uri);
  assert.ok(!uris.includes(`${DIR_A}/from-a.md`), uris.join(','));
  await tree.unmount();
});
