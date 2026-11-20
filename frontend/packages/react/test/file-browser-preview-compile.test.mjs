import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import {
  deferred,
  mount,
  ok,
  recordingFetch,
  settle,
} from './helpers/module-dom.mjs';

const fileTime = '2026-11-11T22:10:00.123Z';

test('with Wiki Dashboard disabled, ancestor tags label Compile files without delaying preview', async () => {
  const target = 'viking://resources/wiki';
  const rootUri = `${target}/nested`;
  const chapter = `${rootUri}/chapter.md`;
  const ordinary = 'viking://resources/ordinary.md';
  const targetAttrs = deferred();
  const ordinaryAttrs = deferred();
  const fetch = recordingFetch(({ path, uri }) => {
    if (path.endsWith('/fs/ls')) return ok([]);
    if (path.endsWith('/fs/stat'))
      return ok({ isDir: false, modTime: fileTime });
    if (path.endsWith('/fs/attrs')) {
      if (uri === ordinary) return ordinaryAttrs.promise;
      if (uri === target) return targetAttrs.promise;
      return ok({ uri, attrs: { tags: [] } });
    }
    return ok('Sample Markdown');
  });
  const page = (uri) =>
    h(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      h(FileBrowser, { uri, rootUri, dashboardKinds: [] }),
    );
  const view = await mount(page(ordinary));
  const meta = () =>
    view.container.querySelector('.ov-file-browser__preview-meta')?.textContent;
  const requested = (uri, path) =>
    fetch.calls.some((call) => call.uri === uri && call.path.endsWith(path));

  try {
    assert.ok(requested(ordinary, '/fs/attrs'));
    assert.ok(requested(ordinary, '/content/read'));
    assert.match(meta(), /15 characters/);
    assert.doesNotMatch(meta(), /AI generated/);
    await act(async () =>
      ordinaryAttrs.resolve(await ok({ attrs: { tags: [] } })),
    );

    await view.render(page(chapter));
    assert.ok(requested(target, '/fs/attrs'));
    assert.ok(requested(chapter, '/content/read'));
    assert.doesNotMatch(meta(), /AI generated/);
    await act(async () =>
      targetAttrs.resolve(
        await ok({ attrs: { tags: ['compile_target=true'] } }),
      ),
    );
    await settle();
    assert.match(meta(), /AI generated/);
  } finally {
    await view.unmount();
  }
});
