import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';

import { OpenVikingProvider } from './helpers/internal-hooks.mjs';
import { useFileBrowser } from './helpers/internal-hooks.mjs';
import { mount, ok, recordingFetch } from './helpers/module-dom.mjs';

const ROOT = 'viking://';
const FILE_A = 'viking://a.md';
const FILE_B = 'viking://b.md';

function probe(api) {
  return function Probe({ uri, controlled }) {
    api.current = useFileBrowser({
      rootUri: ROOT,
      currentUri: uri,
      controlled,
      onUriChange() {},
    });
    return null;
  };
}

const fetchBoth = () =>
  recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') {
      return ok([{ uri: FILE_A }, { uri: FILE_B }]);
    }
    if (call.path === '/api/v1/content/read') return ok('# body\n');
    if (call.path === '/api/v1/fs/stat') return ok({ isDir: false });
    return ok('');
  });

test('a controlled uri drives the selection', async () => {
  // 受控时以宿主给的 URI 为准，模块只上报切换意图。
  //
  // While controlled the host's URI wins and the module only reports switch intent.
  const api = { current: undefined };
  const Probe = probe(api);
  const fetch = fetchBoth();
  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: FILE_A, controlled: true }),
    ),
  );
  assert.equal(api.current.selectedId, FILE_A);
  assert.equal(api.current.previewTabs?.[0]?.key, FILE_A);

  await tree.render(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: FILE_B, controlled: true }),
    ),
  );

  assert.equal(api.current.selectedId, FILE_B);
  assert.equal(api.current.previewTabs?.[0]?.key, FILE_B);
  await tree.unmount();
});

test('an uncontrolled module keeps its own selection', async () => {
  // 非受控时 `currentUri` 为 null 只是「初始没有选中」，之后的选中由模块自己维护。
  // While uncontrolled a null `currentUri` only means "nothing selected initially";
  // later selections are owned by the module.
  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch: fetchBoth() },
      createElement(Probe, { uri: null, controlled: false }),
    ),
  );

  const row = api.current.rows.find((candidate) => candidate.uri === FILE_A);
  await api.current.actions.select(row);
  await tree.render(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch: fetchBoth() },
      createElement(Probe, { uri: null, controlled: false }),
    ),
  );

  assert.equal(api.current.selectedId, FILE_A);
  await tree.unmount();
});

test('an unknown URI is resolved through fs/stat before reading', async () => {
  // 宿主直接传一个尚未展开过的目录 URI 时，它不在树的已知集合里；仅凭这一点按
  // 文件处理会让目录丢掉 L0/L1 摘要、改去读正文。
  //
  // A directory URI the host passes before it was ever expanded is absent from the
  // tree's known set; treating that as a file makes the directory lose its L0/L1
  // summary and read content instead.
  const NESTED = 'viking://resources/unloaded/nested';
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') return ok([]);
    if (call.path === '/api/v1/fs/stat') return ok({ isDir: true });
    if (call.path === '/api/v1/content/abstract') return ok('summary');
    if (call.path === '/api/v1/content/overview') return ok('overview');
    if (call.path === '/api/v1/content/read') return ok('body');
    return ok('');
  });

  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: NESTED, controlled: true }),
    ),
  );

  assert.equal(fetch.countOf('/api/v1/fs/stat'), 1);
  assert.equal(fetch.countOf('/api/v1/content/read'), 0);
  assert.equal(api.current.previewTabs[0].kind, 'directory');
  await tree.unmount();
});

test('a root retry clears the tree-level error once it succeeds', async () => {
  // 恢复之后 `treeError` 必须清掉，否则读它的自定义视图会一直停在失败态。
  // After recovery `treeError` must clear, or a custom view reading it stays failed.
  let failing = true;
  const fetch = recordingFetch((call) => {
    if (call.path !== '/api/v1/fs/ls') return ok('');
    if (failing) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            status: 'error',
            error: { code: 'FORBIDDEN', message: 'denied' },
          }),
          { status: 403, headers: { 'content-type': 'application/json' } },
        ),
      );
    }
    return ok([{ uri: FILE_A }]);
  });

  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: null, controlled: false }),
    ),
  );
  assert.ok(api.current.treeError, 'the first load must fail');

  failing = false;
  await api.current.actions.refresh();
  await tree.render(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: null, controlled: false }),
    ),
  );

  assert.equal(api.current.treeError, null);
  assert.equal(api.current.error, null);
  await tree.unmount();
});
