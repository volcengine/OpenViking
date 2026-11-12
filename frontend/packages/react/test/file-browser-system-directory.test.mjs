import assert from 'node:assert/strict';
import test from 'node:test';
import {
  mount,
  ok,
  recordingFetch,
  tree,
} from './helpers/preview-native-dom.mjs';

const SYSTEM = 'viking://system';
const COMPILE = `${SYSTEM}/compile`;
const EXAMPLES = `${COMPILE}/example-skills`;
const REGULAR = 'viking://resources/example';
const summaryPath = /^\/api\/v1\/content\/(abstract|overview)$/;

test('system directories show no content without summary tabs or requests', async () => {
  const fetch = recordingFetch((call) => {
    if (call.path.endsWith('/fs/ls'))
      return ok([
        { uri: SYSTEM, isDir: true },
        { uri: REGULAR, isDir: true },
      ]);
    if (call.path.endsWith('/fs/stat')) return ok({ isDir: true });
    return ok('');
  });
  const render = (uri) =>
    tree(fetch, uri, {
      rootUri: 'viking://',
      locale: 'zh-CN',
      dashboardKinds: [],
    });
  const view = await mount(render(REGULAR));
  const levels = () =>
    Array.from(
      view.container.querySelectorAll('.ov-file-preview__level'),
      (item) => item.textContent.trim(),
    );
  try {
    assert.deepEqual(levels(), ['L0 摘要', 'L1 总览']);
    for (const uri of [SYSTEM, COMPILE, EXAMPLES]) {
      await view.render(render(uri));
      assert.deepEqual(levels(), []);
      const state = view.container.querySelector(
        '.ov-file-preview__state-title',
      );
      assert.equal(state?.textContent, '暂无内容');
      assert.equal(
        view.container.querySelector('.ov-file-preview__footer')?.textContent,
        undefined,
      );
    }
    const summaryCalls = fetch.calls.filter((call) =>
      summaryPath.test(call.path),
    );
    assert.ok(summaryCalls.length >= 2);
    assert.ok(summaryCalls.every((call) => call.uri === REGULAR));
  } finally {
    await view.unmount();
  }
});
