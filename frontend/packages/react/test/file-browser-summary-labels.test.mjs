import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement } from 'react';

import {
  OpenVikingProvider,
  useFileBrowser,
} from './helpers/internal-hooks.mjs';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';
import { click, fixture, tree } from './helpers/preview-native-dom.mjs';

const ROOT = 'viking://';
const DIR = 'viking://dir';

/**
 * 宿主传入的中文摘要文案。一个真实的宿主会把它们映射到自己的 i18n。
 * Chinese summary copy a host would supply; a real host maps these to its own i18n.
 */
const zhSummary = {
  l0: '摘要',
  l1: '总览',
  failure: '摘要加载失败',
};

function probe(api) {
  return function Probe() {
    api.current = useFileBrowser({
      rootUri: ROOT,
      currentUri: DIR,
      controlled: true,
      summaryLabels: zhSummary,
      onUriChange() {},
    });
    return null;
  };
}

test('the summary failure fallback uses the host’s label', async () => {
  // 兜底文案必须来自可替换文案：写死英文会让中文界面里冒出一句英文。
  // The fallback must come from replaceable copy: a hard-coded English line would leak
  // into a Chinese interface.
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') return ok([{ uri: DIR, isDir: true }]);
    if (call.path === '/api/v1/content/abstract') return ok('好的 L0');
    if (call.path === '/api/v1/content/overview') {
      // 服务端返回空消息，正是触发兜底的那一种失败。
      // The server reports an empty message, the failure that triggers the fallback.
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
      createElement(Probe, null),
    ),
  );

  const tab = api.current.previewTabs[0];
  assert.equal(tab.kind, 'directory');
  assert.equal(tab.levels[0].label, '摘要');
  assert.equal(tab.levels[0].source.content, '好的 L0');
  // 失败的那一级用宿主给的兜底文案，不是英文。
  // The failed level uses the host's fallback copy rather than English.
  assert.match(tab.levels[1].source.content, /摘要加载失败/);
  assert.doesNotMatch(tab.levels[1].source.content, /could not be loaded/);
  await tree.unmount();
});

test('a non-empty server message still wins over the fallback', async () => {
  // 有具体原因时显示原因，兜底只在服务端没给消息时使用。
  // A concrete reason is shown as-is; the fallback only applies when the server gave
  // no message.
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls') return ok([{ uri: DIR, isDir: true }]);
    if (call.path === '/api/v1/content/abstract') return ok('好的 L0');
    if (call.path === '/api/v1/content/overview') {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            status: 'error',
            error: { code: 'FORBIDDEN', message: '权限不足' },
          }),
          { status: 403, headers: { 'content-type': 'application/json' } },
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
      createElement(Probe, null),
    ),
  );

  assert.match(api.current.previewTabs[0].levels[1].source.content, /权限不足/);
  await tree.unmount();
});

test('directory placeholders become empty summaries without removing real Markdown', async () => {
  const placeholder =
    '# viking://resources/team-docs [Directory abstract is not ready]';
  let overview =
    '# viking://resources/team-docs\n\n[Directory overview is not ready]';
  const fetch = recordingFetch(({ path }) => {
    if (path === '/api/v1/fs/ls') return ok([{ uri: DIR, isDir: true }]);
    if (path === '/api/v1/content/abstract') return ok(placeholder);
    if (path === '/api/v1/content/overview') return ok(overview);
    return ok('');
  });

  const api = { current: undefined };
  const Probe = probe(api);
  const tree = await mount(
    createElement(OpenVikingProvider, { fetch }, createElement(Probe)),
  );

  try {
    const contents = () =>
      api.current.previewTabs[0].levels.map(({ source }) => source.content);
    assert.deepEqual(contents(), ['', '']);
    for (const value of [
      '# dir\n\n[Directory overview is not ready]',
      '# dir\r\n\r\n[Directory overview is not ready]\r\n',
    ]) {
      overview = value;
      await act(() => api.current.actions.refresh());
      await settle();
      assert.deepEqual(contents(), ['', '']);
    }
    overview = '# other-directory\n\n[Directory overview is not ready]';
    await act(() => api.current.actions.refresh());
    await settle();
    assert.deepEqual(contents(), ['', overview]);
    overview =
      '# viking://resources/team-docs\n\nAn upstream placeholder follows.\n\n[Directory overview is not ready]';
    await act(() => api.current.actions.refresh());
    await settle();
    assert.deepEqual(contents(), ['', overview]);
  } finally {
    await tree.unmount();
  }
});

test('native directory summary placeholders show level-specific Chinese empty states', async () => {
  const uri = 'viking://user/alice/memories/experiences';
  const fetch = fixture(uri, true, (call) => {
    if (call.path.endsWith('/content/abstract'))
      return ok(`# ${uri} [Directory abstract is not ready]`);
    if (call.path.endsWith('/content/overview'))
      return ok(`# ${uri}\n\n[Directory overview is not ready]`);
  });
  const view = await mount(
    tree(fetch, uri, {
      rootUri: 'viking://',
      locale: 'zh-CN',
      dashboardKinds: [],
    }),
  );

  try {
    const levels = [
      ...view.container.querySelectorAll('.ov-file-preview__level'),
    ];
    assert.deepEqual(
      levels.map((level) => level.textContent),
      ['L0 摘要', 'L1 总览'],
    );
    for (const [index, emptyTitle] of [
      '暂无 L0 摘要',
      '暂无 L1 总览',
    ].entries()) {
      await click(levels[index]);
      const panel = view.container.querySelector(
        '.ov-file-preview__body[role="tabpanel"]',
      );
      assert.equal(levels[index].getAttribute('aria-selected'), 'true');
      assert.equal(panel?.textContent?.trim(), emptyTitle);
    }
  } finally {
    await view.unmount();
  }
});

test('directory summary Markdown can mention the native placeholder suffixes', async () => {
  const abstract = `# ${DIR} [Directory abstract is not ready]\n\n这里是摘要的实际正文。`;
  const overview = `# ${DIR}\n\n这里是总览的实际正文。\n\n[Directory overview is not ready]`;
  const fetch = recordingFetch((call) => {
    if (call.path.endsWith('/fs/ls')) return ok([{ uri: DIR, isDir: true }]);
    if (call.path.endsWith('/content/abstract')) return ok(abstract);
    if (call.path.endsWith('/content/overview')) return ok(overview);
    return ok('');
  });
  const api = { current: undefined };
  const view = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(probe(api)),
    ),
  );

  try {
    assert.deepEqual(
      api.current.previewTabs[0].levels.map((level) => level.source.content),
      [abstract, overview],
    );
  } finally {
    await view.unmount();
  }
});
