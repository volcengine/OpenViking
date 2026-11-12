import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h, createRef } from 'react';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { OpenVikingProvider } from '@openviking/react/client';
import { mount, ok, recordingFetch } from './helpers/module-dom.mjs';
import { command, scrolled } from './helpers/file-browser-reveal.mjs';

const canonical = 'viking://user/default/resources';
const alias = 'viking://~/resources';

for (const { rootUri, listedRoot, targetRoot, explicitUser } of [
  {
    rootUri: alias,
    listedRoot: alias,
    targetRoot: canonical,
    explicitUser: true,
  },
  { rootUri: canonical, listedRoot: canonical, targetRoot: alias },
  { rootUri: canonical, listedRoot: alias, targetRoot: canonical },
]) {
  test(`reveals ${targetRoot} under ${rootUri} with rows from ${listedRoot}`, async () => {
    scrolled.length = 0;
    const target = `${targetRoot}/nested/result.png`;
    const directory = `${listedRoot}/nested`;
    const row = `${directory}/result.png`;
    const fetch = recordingFetch(({ path, uri }) => {
      if (path === '/api/v1/fs/ls' && uri === rootUri)
        return ok([{ uri: directory, isDir: true }]);
      if (path === '/api/v1/fs/ls' && uri === directory)
        return ok([{ uri: row }]);
      if (path === '/api/v1/fs/stat' && uri === 'viking://~')
        return ok({ uri: 'viking://user/default', isDir: true });
      if (path === '/api/v1/fs/stat') return ok({ uri, isDir: false });
      return ok('');
    });
    const handle = createRef();
    const view = await mount(
      h(
        OpenVikingProvider,
        { fetch, userId: explicitUser ? 'inherited' : undefined },
        h(FileBrowser, {
          ref: handle,
          rootUri,
          dashboardKinds: [],
          requestOptions: explicitUser ? { userId: 'default' } : undefined,
        }),
      ),
    );
    try {
      assert.equal(await command(handle, target), true);
      assert.equal(
        view.container
          .querySelector('[data-file-tree-row-id][aria-selected="true"]')
          ?.getAttribute('data-file-tree-row-id'),
        row,
      );
      assert.deepEqual(scrolled, [row]);
      assert.deepEqual(
        fetch.calls
          .filter((call) => call.path === '/api/v1/fs/ls')
          .map((call) => call.uri),
        [rootUri, directory],
      );
      assert.equal(await command(handle, target), true);
      assert.equal(
        fetch.calls.filter(
          (call) =>
            call.path === '/api/v1/fs/stat' && call.uri === 'viking://~',
        ).length,
        explicitUser ? 0 : 1,
      );
      const lists = fetch.countOf('/api/v1/fs/ls');
      assert.equal(
        await command(
          handle,
          'viking://user/other/resources/nested/result.png',
        ),
        false,
      );
      assert.equal(fetch.countOf('/api/v1/fs/ls'), lists);
      assert.deepEqual(scrolled, [row, row]);
    } finally {
      await view.unmount();
    }
  });
}
