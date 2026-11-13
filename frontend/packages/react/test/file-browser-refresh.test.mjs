import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';

import { OpenVikingProvider } from './helpers/internal-hooks.mjs';
import { OpenVikingProvider as PublicOpenVikingProvider } from '@openviking/react/client';
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
// 这些用例都在切换宿主给的 URI，因此用受控模式：非受控时 `currentUri` 只是初值，
// 切换后应以模块自己的 `pendingUri` 为准，那属于另一组用例（见 selection 测试）。
//
// These cases switch the URI the host passes, so they run controlled: while
// uncontrolled `currentUri` is only the initial value and later selections follow the
// module's own `pendingUri`, which the selection tests cover separately.
function probe(api) {
  return function Probe({ uri, notify }) {
    api.current = useFileBrowser({
      rootUri: ROOT,
      controlled: true,
      currentUri: uri,
      onUriChange: notify,
    });
    return null;
  };
}

test('refresh reloads the preview as well as the listings', async () => {
  // 只刷树会让右侧继续显示旧正文；外部改动之后那是最容易误导人的状态。
  // Reloading only the tree leaves the detail pane on old content, the most
  // misleading state after an external change.
  let body = '# old\n';
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') return listing([{ uri: FILE_B }]);
    if (call.path === '/api/v1/content/read') return ok(body);
    return ok({});
  });

  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: FILE_B, notify() {} }),
    ),
  );
  const before = fetch.countOf('/api/v1/content/read');

  body = '# new\n';
  await api.current.actions.refresh();
  await tree.render(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: FILE_B, notify() {} }),
    ),
  );

  assert.equal(fetch.countOf('/api/v1/content/read'), before + 1);
  assert.match(api.current.previewTabs[0].source.content, /# new/);
  await tree.unmount();
});

test('the root and a collapsed directory still read a summary', async () => {
  // 根目录从不出现在 `rows` 里，祖先折叠后其后代也会消失；按可见行判定类型会
  // 把它们误判成文件，丢掉 L0/L1 摘要改去读正文。
  //
  // The root never appears in `rows`, and a descendant disappears when its ancestor
  // collapses; deciding the type from visible rows would misread both as files and
  // read content instead of their L0/L1 summary.
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') {
      if (call.uri === ROOT) return listing([{ uri: DIR_A, isDir: true }]);
      return listing([{ uri: `${DIR_A}/nested`, isDir: true }]);
    }
    if (call.path === '/api/v1/content/abstract') return ok('summary');
    if (call.path === '/api/v1/content/overview') return ok('overview');
    if (call.path === '/api/v1/content/read') return ok('body');
    return ok({});
  });

  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: ROOT, notify() {} }),
    ),
  );

  assert.equal(api.current.previewTabs[0].kind, 'directory');
  assert.equal(fetch.countOf('/api/v1/content/read'), 0);

  // 展开再折叠，后代从 `rows` 里消失，但类型不变。
  // Expand then collapse: the descendant leaves `rows` while keeping its type.
  const dirRow = api.current.rows.find((row) => row.uri === DIR_A);
  await api.current.actions.toggleExpand(dirRow);
  await api.current.actions.toggleExpand(dirRow);
  await tree.render(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: `${DIR_A}/nested`, notify() {} }),
    ),
  );

  assert.equal(api.current.previewTabs[0].kind, 'directory');
  assert.equal(fetch.countOf('/api/v1/content/read'), 0);
  await tree.unmount();
});

test('an aborted directory read does not keep its stale level', async () => {
  // 目录的 L0 先回来、L1 还在路上时切换资源：被中止的 L1 此前会被当成「没有
  // 生成」，让一个过期目录页签成功落地并盖住新选中的文件。
  //
  // With a directory's L0 resolved and L1 still in flight, switching resources made
  // the aborted L1 look like "not generated", letting an obsolete directory tab land
  // and overwrite the newly selected file.
  const slowOverview = deferred();
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') {
      return listing([{ uri: DIR_A, isDir: true }, { uri: FILE_B }]);
    }
    if (call.path === '/api/v1/content/abstract') return ok('A summary');
    if (call.path === '/api/v1/content/overview') return slowOverview.promise;
    if (call.path === '/api/v1/content/read') return ok('# B\n');
    // FILE_B 在初始 listing 里出现过，类型已知；这里补 stat 只是兜底，避免类型
    // 未知时多一次查询把断言拖进另一条路径。
    // FILE_B appears in the initial listing so its type is known; this stat answer is
    // only a fallback so an unknown-type lookup cannot pull the assertion elsewhere.
    if (call.path === '/api/v1/fs/stat') return ok({ isDir: false });
    return ok({});
  });

  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: DIR_A, notify() {} }),
    ),
  );

  await tree.render(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: FILE_B, notify() {} }),
    ),
  );
  // 迟到的 L1 在切换之后才回来。
  // The late L1 only resolves after the switch.
  slowOverview.resolve(ok('overview'));
  await tree.render(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: FILE_B, notify() {} }),
    ),
  );

  assert.equal(api.current.previewTabs[0].kind, 'file');
  await tree.unmount();
});

test('a failed listing renders an error row with a retry, not an empty tree', async () => {
  // 403 与「这个目录是空的」在界面上完全不同；混成空态会让人以为数据本来就没了。
  // A 403 and "this directory is empty" are different situations; merging them into
  // the empty state reads as though the data never existed.
  const fetch = recordingFetch(() =>
    Promise.resolve(
      new Response(
        JSON.stringify({
          status: 'error',
          error: { code: 'FORBIDDEN', message: 'Permission denied' },
        }),
        { status: 403, headers: { 'content-type': 'application/json' } },
      ),
    ),
  );

  const { FileBrowser } =
    await import('@openviking/react/modules/file-browser');
  const tree = await mount(
    createElement(
      PublicOpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(FileBrowser, null),
    ),
  );

  const html = tree.container.innerHTML;
  assert.match(html, /Permission denied|加载失败|Failed to load/);
  assert.doesNotMatch(html, /ov-file-tree__state[^]*No files/);
  await tree.unmount();
});

test('a failed read renders a failure state instead of the unselected artwork', async () => {
  // 有选中的资源却读不出来，必须显示原因与重试；画成空态会让一次 403 看起来像
  // 「还没选文件」。
  //
  // A selected resource that cannot be read needs the reason and a retry; painting
  // it as the empty state makes a 403 look like "no file selected yet".
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') {
      return ok([{ uri: 'viking://b.md' }]);
    }
    if (call.path === '/api/v1/content/read') {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            status: 'error',
            error: { code: 'FORBIDDEN', message: 'Permission denied' },
          }),
          { status: 403, headers: { 'content-type': 'application/json' } },
        ),
      );
    }
    return ok({});
  });

  const { FileBrowser } =
    await import('@openviking/react/modules/file-browser');
  const tree = await mount(
    createElement(
      PublicOpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(FileBrowser, { defaultUri: 'viking://b.md' }),
    ),
  );

  const html = tree.container.innerHTML;
  assert.match(html, /ov-file-browser__failure/);
  assert.match(html, /Permission denied/);
  assert.match(html, /ov-file-browser__failure-retry/);
  await tree.unmount();
});
