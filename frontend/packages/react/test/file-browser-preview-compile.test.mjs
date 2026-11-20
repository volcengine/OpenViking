import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { mount, ok, recordingFetch } from './helpers/module-dom.mjs';

const fileTime = '2026-11-11T22:10:00.123Z';

for (const dashboardKinds of [undefined, []]) {
  test(`Compile preview uses file metadata and legacy ownership (${dashboardKinds ?? 'default'})`, async () => {
    const target = 'viking://resources/wiki';
    const chapter = `${target}/chapter.md`;
    const ordinary = `${target}/README.md`;
    const directory = `${target}/nested`;
    const unrelated = 'viking://resources/unrelated/nested';
    const legacy = 'viking://resources/legacy/chapter.md';
    const legacyTarget = 'viking://resources/legacy';

    const fetch = recordingFetch(({ path, uri }) => {
      if (path.endsWith('/fs/ls')) return ok([]);
      if (path.endsWith('/fs/stat'))
        return ok({ isDir: uri === directory, modTime: fileTime });
      if (path.endsWith('/fs/attrs'))
        return ok({
          uri,
          attrs: { tags: uri === target ? ['compile_target=true'] : [] },
        });
      if (path.endsWith('/compile/configs'))
        return ok({ targets: [{ to: legacyTarget }, { to: unrelated }] });
      if (path.endsWith('/content/read'))
        return ok(
          uri === chapter || uri === legacy
            ? 'Sample Markdown\n<!-- CONFLICTS_CONTENT\n-->'
            : 'Sample Markdown',
        );
      return ok('');
    });
    const page = (uri) =>
      h(
        OpenVikingProvider,
        { baseURL: 'https://ov.test', fetch },
        h(FileBrowser, { uri, dashboardKinds }),
      );
    const view = await mount(page(ordinary));
    const meta = () =>
      view.container.querySelector('.ov-file-browser__preview-meta')
        ?.textContent;
    const attrs = (uri) =>
      fetch.calls.filter(
        (call) => call.uri === uri && call.path.endsWith('/fs/attrs'),
      ).length;

    try {
      assert.match(meta(), /15 characters/);
      assert.doesNotMatch(meta(), /AI generated/);
      assert.equal(attrs(ordinary), dashboardKinds ? 0 : 1);
      assert.equal(attrs(target), 0);

      await view.render(page(directory));
      assert.doesNotMatch(meta(), /AI generated/);
      assert.equal(attrs(target), 0);

      await view.render(page(chapter));
      assert.match(meta(), /AI generated/);
      assert.equal(attrs(chapter), 1);
      assert.equal(attrs(target), 1);

      await view.render(page(legacy));
      assert.match(meta(), /AI generated/);
      assert.equal(attrs(legacy), 1);
    } finally {
      await view.unmount();
    }
  });
}
