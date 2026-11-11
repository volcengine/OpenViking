import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';

import { OpenVikingProvider } from './helpers/internal-hooks.mjs';
// 从打包产物导入：Hook 内部的相对引用带 `.js` 后缀，只有构建后的目录能解析。
// Import from the built package: the Hook's relative imports carry a `.js` suffix,
// which only resolves inside the build output.
import { useFileBrowser } from './helpers/internal-hooks.mjs';
import { deferred, mount, ok, recordingFetch } from './helpers/module-dom.mjs';

const ROOT = 'viking://';
const DIR_A = 'viking://a';
const FILE_B = 'viking://b.md';

/** 一次目录列表应答。One directory listing response. */
const listing = (entries) => ok(entries);

/**
 * 挂载一个只把 Hook 结果交给回调的探针组件：模块的返回值不是渲染结果，测试要
 * 看的是每次提交后的 `api`。
 *
 * Mounts a probe that hands the Hook's result to a callback: the module's return
 * value is not markup, and what the tests inspect is `api` after each commit.
 */
function probe(api) {
  return function Probe({ uri, notify }) {
    api.current = useFileBrowser({
      rootUri: ROOT,
      currentUri: uri,
      onUriChange: notify,
    });
    return null;
  };
}

test('a scope switch drops the previous account’s expansion', async () => {
  // 账号 A 的展开请求还在路上时切到账号 B：A 的子节点不能落进 B 的树。
  // Switching to account B while A's expansion is in flight must not let A's
  // children land in B's tree.
  const slow = deferred();
  let scope = 'A';
  const fetch = recordingFetch((call) => {
    if (call.path !== '/api/v1/fs/ls') return ok({});
    if (call.uri === DIR_A && scope === 'A') return slow.promise;
    return listing([{ uri: `${DIR_A}/from-b.md` }]);
  });

  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch, accountId: 'A' },
      createElement(Probe, { uri: DIR_A, notify() {} }),
    ),
  );

  await tree.render(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch, accountId: 'B' },
      createElement(Probe, { uri: DIR_A, notify() {} }),
    ),
  );
  // A 的响应在切换之后才回来。
  // A's response only resolves after the switch.
  scope = 'B';
  slow.resolve(listing([{ uri: `${DIR_A}/from-a.md` }]));
  await tree.render(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch, accountId: 'B' },
      createElement(Probe, { uri: DIR_A, notify() {} }),
    ),
  );

  const uris = api.current.rows.map((row) => row.uri);
  assert.ok(!uris.includes(`${DIR_A}/from-a.md`), uris.join(','));
  await tree.unmount();
});

test('an inline onUriChange does not reload the preview forever', async () => {
  // 宿主传内联箭头函数时，回调引用每轮都变；若预览的取数 effect 依赖它，一次
  // 选中就会滚成无限重载。
  //
  // An inline arrow function changes identity every render; if the preview's loading
  // effect depended on it, one selection would roll into an endless reload.
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') return listing([{ uri: FILE_B }]);
    if (call.path === '/api/v1/content/read') return ok('# B\n');
    return ok({});
  });

  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: FILE_B, notify: () => {} }),
    ),
  );

  const readsAfterFirstCommit = fetch.countOf('/api/v1/content/read');
  // 再提交两次，回调每轮都是新的内联函数。
  // Commit twice more, each with a fresh inline callback.
  for (let index = 0; index < 2; index++) {
    await tree.render(
      createElement(
        OpenVikingProvider,
        { baseURL: 'https://ov.test', fetch },
        createElement(Probe, { uri: FILE_B, notify: () => {} }),
      ),
    );
  }

  assert.equal(
    fetch.countOf('/api/v1/content/read'),
    readsAfterFirstCommit,
    'the preview must not reload on a callback identity change',
  );
  await tree.unmount();
});

test('a slow listing does not overwrite a newer selection', async () => {
  // 首次展开要发请求；等它回来才上报选中，会把用户随后点的文件改回目录。
  // A first expansion issues a request; reporting only after it returns would
  // revert a file the user picked in the meantime.
  const slow = deferred();
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') {
      if (call.uri === DIR_A) return slow.promise;
      return listing([{ uri: DIR_A, isDir: true }, { uri: FILE_B }]);
    }
    if (call.path === '/api/v1/content/read') return ok('# B\n');
    if (call.path === '/api/v1/content/abstract') return ok('A summary');
    return ok('');
  });

  const notified = [];
  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, {
        uri: null,
        notify: (uri) => notified.push(uri),
      }),
    ),
  );

  const directoryRow = api.current.rows.find((row) => row.uri === DIR_A);
  const fileRow = api.current.rows.find((row) => row.uri === FILE_B);
  // 先点目录（请求挂住），再点文件。
  // Click the directory first (its request hangs), then the file.
  void api.current.actions.select(directoryRow);
  await api.current.actions.select(fileRow);
  slow.resolve(listing([]));
  await tree.render(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, {
        uri: null,
        notify: (uri) => notified.push(uri),
      }),
    ),
  );

  assert.deepEqual(notified, [DIR_A, FILE_B]);
  await tree.unmount();
});
