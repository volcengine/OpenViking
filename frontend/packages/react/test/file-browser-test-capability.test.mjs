import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

for (const removal of ['loader', 'data source']) {
  test(`removing the test ${removal} expires the selected tab and page`, async () => {
    const directory = 'viking://user/alice/memories/experiences';
    const uri = `${directory}/check.md`;
    const fetch = recordingFetch(({ path, uri: target }) => {
      if (path.endsWith('/ls')) return ok([{ uri, name: 'Experience' }]);
      if (path.endsWith('/stat'))
        return ok({ uri: target, isDir: target === directory });
      if (path.endsWith('/attrs')) return ok({ attrs: { tags: [] } });
      if (path.endsWith('/outcomes')) return ok({ outcome_distribution: [] });
      if (path.endsWith('/trajectories'))
        return ok({ items: [], total: 0, has_more: false });
      return ok('Document');
    });
    const offsets = [];
    const dataSource = {
      async tests({ offset }) {
        offsets.push(offset);
        return {
          items: [
            {
              id: String(offset),
              title: 'Material',
              content: `Material ${offset}`,
            },
          ],
          total: 21,
        };
      },
    };
    const render = (experienceDataSource) =>
      h(
        OpenVikingProvider,
        { fetch },
        h(FileBrowser, {
          rootUri: directory,
          uri,
          dashboardKinds: ['experience'],
          experienceDataSource,
        }),
      );
    const view = await mount(render(dataSource));
    const tab = (label) =>
      [...view.container.querySelectorAll('[role="tab"]')].find(
        (element) => element.textContent === label,
      );
    const click = async (element) => {
      assert.ok(element);
      await act(async () => element.click());
      await settle();
    };
    try {
      await click(tab('Experience dashboard'));
      await click(tab('Test'));
      await click(
        view.container.querySelector('button[aria-label="Next page"]'),
      );
      assert.match(view.container.textContent, /Material 20/);
      await view.render(
        render(
          removal === 'loader'
            ? { ...dataSource, tests: undefined }
            : undefined,
        ),
      );
      assert.equal(tab('Test'), undefined);
      assert.equal(
        tab('Application impact').getAttribute('aria-selected'),
        'true',
      );
      await view.render(render(dataSource));
      assert.equal(
        tab('Application impact').getAttribute('aria-selected'),
        'true',
      );
      assert.deepEqual(offsets, [0, 20]);
      await click(tab('Test'));
      assert.match(view.container.textContent, /Material 0/);
      assert.deepEqual(offsets, [0, 20, 0]);
    } finally {
      await view.unmount();
    }
  });
}
