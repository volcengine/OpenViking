import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement } from 'react';
import { OpenVikingProvider as PublicOpenVikingProvider } from '@openviking/react/client';
import { mount, ok, recordingFetch } from './helpers/module-dom.mjs';

const FILE_B = 'viking://b.md';

test('the host can refresh native content and add a specialized resource tab', async () => {
  const fetch = recordingFetch(({ path }) => {
    if (path === '/api/v1/fs/ls') return ok([{ uri: FILE_B }]);
    if (path === '/api/v1/content/read') return ok('');
    return ok({});
  });
  const { FileBrowser } =
    await import('@openviking/react/modules/file-browser');
  const render = (refreshKey) =>
    createElement(
      PublicOpenVikingProvider,
      { fetch },
      createElement(FileBrowser, {
        uri: FILE_B,
        refreshKey,
        dashboardKinds: [],
        renderResourceTab: ({ uri, isDirectory, content }) => {
          assert.equal(uri, FILE_B);
          assert.equal(isDirectory, false);
          assert.equal(content, '');
          return { title: 'Host view', content: 'Specialized resource' };
        },
      }),
    );
  const tree = await mount(render(0));
  const tab = () =>
    [...tree.container.querySelectorAll('[role="tab"]')].find(
      (element) => element.textContent === 'Host view',
    );
  assert.ok(tab());
  const before = fetch.countOf('/api/v1/content/read');
  await tree.render(render(1));
  assert.ok(fetch.countOf('/api/v1/content/read') > before);
  await act(async () => tab().click());
  assert.match(
    tree.container.querySelector('[role="tabpanel"]').textContent,
    /Specialized resource/,
  );
  await tree.unmount();
});
