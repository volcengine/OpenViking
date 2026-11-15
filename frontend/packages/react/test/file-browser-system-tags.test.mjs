import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';

import { OpenVikingProvider } from './helpers/internal-hooks.mjs';
// 从打包产物导入：Hook 内部的相对引用带 `.js` 后缀，只有构建后的目录能解析。
// Import from the built package: the Hook's relative imports carry a `.js` suffix,
// which only resolves inside the build output.
import { useFileBrowser } from './helpers/internal-hooks.mjs';
import { mount, ok, recordingFetch } from './helpers/module-dom.mjs';

const ROOT = 'viking://';
const SYSTEM = 'viking://system';
const DIR_A = 'viking://a';

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

/** 目录列表请求里是否带了 `include_tags`。Whether a listing sent `include_tags`. */
const sendsTags = (call) => Object.hasOwn(call.query, 'include_tags');

test('a system listing omits include_tags and still renders its children', async () => {
  // `viking://system` 是 FILE_ONLY 命名空间，不写向量库；带上 `include_tags`
  // 会让服务端把整个列表请求判为非法参数（400），于是目录展开成「加载失败」。
  // 展开后必须能拿到子目录，且请求里不能有这个参数。
  //
  // `viking://system` is a FILE_ONLY namespace that never writes to the vector
  // store; sending `include_tags` makes the server reject the whole listing as an
  // invalid argument (400), so the directory opens as "load failed". Expanding it
  // must return its children, and the request must not carry the parameter.
  const fetch = recordingFetch((call) => {
    if (call.path !== '/api/v1/fs/ls') return ok({});
    if (call.uri === ROOT) return listing([{ uri: SYSTEM, isDir: true }]);
    return listing([{ uri: `${SYSTEM}/compile`, isDir: true }]);
  });

  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: null, notify() {} }),
    ),
  );

  const systemRow = api.current.rows.find((row) => row.uri === SYSTEM);
  assert.ok(systemRow, 'the root listing must contain the system directory');
  await api.current.actions.toggleExpand(systemRow);
  await tree.render(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: null, notify() {} }),
    ),
  );

  const systemCalls = fetch.calls.filter((call) => call.uri === SYSTEM);
  assert.equal(systemCalls.length, 1);
  assert.equal(sendsTags(systemCalls[0]), false);
  assert.ok(
    api.current.rows.some((row) => row.uri === `${SYSTEM}/compile`),
    api.current.rows.map((row) => row.uri).join(','),
  );
  await tree.unmount();
});

test('a system descendant listing omits include_tags too', async () => {
  // 判定按子树生效，不能只认 `viking://system` 这一层：`system/compile` 同样是
  // FILE_ONLY，展开它也会被 400 挡住。
  //
  // The check covers the subtree, not just `viking://system` itself:
  // `system/compile` is FILE_ONLY as well and expanding it fails the same way.
  const fetch = recordingFetch((call) => {
    if (call.path !== '/api/v1/fs/ls') return ok({});
    if (call.uri === ROOT) return listing([{ uri: SYSTEM, isDir: true }]);
    if (call.uri === SYSTEM)
      return listing([{ uri: `${SYSTEM}/compile`, isDir: true }]);
    return listing([{ uri: `${SYSTEM}/compile/notes.md` }]);
  });

  const api = { current: undefined };
  const Probe = probe(api);
  const mountTree = () =>
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: null, notify() {} }),
    );

  const tree = await mount(mountTree());
  await api.current.actions.toggleExpand(
    api.current.rows.find((row) => row.uri === SYSTEM),
  );
  await tree.render(mountTree());
  await api.current.actions.toggleExpand(
    api.current.rows.find((row) => row.uri === `${SYSTEM}/compile`),
  );
  await tree.render(mountTree());

  const nested = fetch.calls.filter((call) => call.uri === `${SYSTEM}/compile`);
  assert.equal(nested.length, 1);
  assert.equal(sendsTags(nested[0]), false);
  await tree.unmount();
});

test('an indexed directory still asks for tags', async () => {
  // Wiki 看板靠列表里的 `compile_target` / `has_conflict` 标签判定入口，索引
  // 命名空间必须继续请求它们；否则看板入口会整体消失。
  //
  // The Wiki dashboard decides its entry from the `compile_target` /
  // `has_conflict` tags in the listing, so indexed namespaces must keep asking for
  // them; otherwise every dashboard entry disappears.
  const fetch = recordingFetch((call) => {
    if (call.path !== '/api/v1/fs/ls') return ok({});
    if (call.uri === ROOT) return listing([{ uri: DIR_A, isDir: true }]);
    return listing([{ uri: `${DIR_A}/note.md` }]);
  });

  const api = { current: undefined };
  const Probe = probe(api);
  const mountTree = () =>
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe, { uri: null, notify() {} }),
    );

  const tree = await mount(mountTree());
  await api.current.actions.toggleExpand(
    api.current.rows.find((row) => row.uri === DIR_A),
  );
  await tree.render(mountTree());

  const rootCall = fetch.calls.find((call) => call.uri === ROOT);
  const dirCall = fetch.calls.find((call) => call.uri === DIR_A);
  assert.equal(rootCall.query.include_tags, 'true');
  assert.equal(dirCall.query.include_tags, 'true');
  await tree.unmount();
});
