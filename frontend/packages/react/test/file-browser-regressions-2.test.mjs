import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement } from 'react';

import { OpenVikingProvider } from './helpers/internal-hooks.mjs';
import { useFileBrowser } from './helpers/internal-hooks.mjs';
import { mount, ok, recordingFetch } from './helpers/module-dom.mjs';

const ROOT = 'viking://';
const FILE_A = 'viking://a.md';
const FILE_B = 'viking://b.md';

function probe(api) {
  return function Probe({ uri, controlled, notify }) {
    api.current = useFileBrowser({
      rootUri: ROOT,
      currentUri: uri,
      controlled,
      onUriChange: notify ?? (() => {}),
    });
    return null;
  };
}

const filesFetch = () =>
  recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') {
      return ok([{ uri: FILE_A }, { uri: FILE_B }]);
    }
    if (call.path === '/api/v1/content/read') return ok('# body\n');
    if (call.path === '/api/v1/fs/stat') return ok({ isDir: false });
    return ok('');
  });

test('an uncontrolled selection replaces a non-null initial currentUri', async () => {
  // 非受控时 `currentUri` 只是初值。写成 `currentUri ?? pendingUri` 会让任何非空
  // 初值永久压过模块自己的选中，用户点别的文件也没反应。
  //
  // While uncontrolled `currentUri` is only the initial value. Writing
  // `currentUri ?? pendingUri` lets any non-null initial value win forever, so picking
  // another file does nothing.
  const fetch = filesFetch();
  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: FILE_A, controlled: false }),
    ),
  );
  assert.equal(api.current.selectedId, FILE_A);

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

  assert.equal(api.current.selectedId, FILE_B);
  assert.equal(api.current.previewTabs[0].key, FILE_B);
  await tree.unmount();
});

test('a root retry that fails again restores treeError', async () => {
  // 加载开始时清了整树级错误；再次失败时必须写回去，否则读 `treeError` 的自定义
  // 视图会把一次失败当成已恢复。
  //
  // Loading clears the tree-level error; a repeated failure must restore it, or a
  // custom view reading `treeError` treats a failure as recovered.
  const denied = () =>
    Promise.resolve(
      new Response(
        JSON.stringify({
          status: 'error',
          error: { code: 'FORBIDDEN', message: 'still denied' },
        }),
        { status: 403, headers: { 'content-type': 'application/json' } },
      ),
    );
  const fetch = recordingFetch((call) =>
    call.path === '/api/v1/fs/ls' ? denied() : ok(''),
  );

  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: null, controlled: true }),
    ),
  );
  await act(() => new Promise((resolve) => setTimeout(resolve, 1100)));
  assert.ok(api.current.treeError, 'the first load must fail');

  await api.current.actions.refresh();
  await tree.render(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: null, controlled: true }),
    ),
  );

  assert.ok(api.current.treeError, 'a repeated failure must keep the error');
  assert.match(api.current.treeError.message, /still denied/);
  await tree.unmount();
});
