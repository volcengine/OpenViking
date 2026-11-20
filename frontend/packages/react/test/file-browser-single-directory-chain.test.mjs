import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import {
  mount,
  ok,
  recordingFetch,
  settle,
  deferred,
} from './helpers/module-dom.mjs';

const root = 'viking://resources';
const folder = `${root}/folder`;
const child = `${folder}/child`;
const leaf = `${child}/leaf`;
const dir = (uri, access) => ({ uri, isDir: true, access });
const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);
const toggle = async (view, uri) => {
  await act(async () =>
    row(view, uri).querySelector('[data-role="toggle"]').click(),
  );
  await settle();
};
function fixture(children, extra = () => undefined) {
  return recordingFetch((call) => {
    if (call.path !== '/api/v1/fs/ls') return ok('');
    const response = extra(call);
    if (response) return response;
    return ok(call.uri === root ? [dir(folder)] : (children[call.uri] ?? []));
  });
}
const element = (fetch, accountId = 'A') =>
  h(
    OpenVikingProvider,
    { fetch, accountId, baseURL: 'https://ov.test' },
    h(FileBrowser, { rootUri: root, dashboardKinds: [] }),
  );

test('opening one folder expands its single-directory chain and can still fold it', async () => {
  const fetch = fixture({
    [folder]: [dir(child)],
    [child]: [dir(leaf)],
    [leaf]: [{ uri: `${leaf}/a.md` }, { uri: `${leaf}/b.md` }],
  });
  const view = await mount(element(fetch));
  try {
    assert.equal(row(view, folder).getAttribute('aria-expanded'), 'false');
    await toggle(view, folder);
    assert.equal(row(view, child).getAttribute('aria-expanded'), 'true');
    assert.equal(row(view, leaf).getAttribute('aria-expanded'), 'true');
    assert.ok(row(view, `${leaf}/a.md`));
    await toggle(view, folder);
    assert.equal(row(view, child) === null, true);
    assert.equal(row(view, folder).getAttribute('aria-expanded'), 'false');
  } finally {
    await view.unmount();
  }
});

for (const [name, entries] of [
  ['siblings', [dir(child), dir(`${folder}/other`)]],
  ['file sibling', [dir(child), { uri: `${folder}/a.md` }]],
  ['single file', [{ uri: `${folder}/a.md` }]],
  ['denied child', [dir(child, 'denied')]],
])
  test(`automatic expansion stops at ${name}`, async () => {
    const fetch = fixture({ [folder]: entries });
    const view = await mount(element(fetch));
    try {
      await toggle(view, folder);
      assert.equal(
        fetch.calls.some((c) => c.path === '/api/v1/fs/ls' && c.uri === child),
        false,
      );
    } finally {
      await view.unmount();
    }
  });

for (const action of ['fold', 'account'])
  test(`a delayed child cannot continue after ${action}`, async () => {
    const pending = deferred();
    const fetch = fixture({ [folder]: [dir(child)] }, (call) =>
      call.uri === child ? pending.promise : undefined,
    );
    const view = await mount(element(fetch));
    try {
      await toggle(view, folder);
      if (action === 'fold') await toggle(view, folder);
      else await view.render(element(fetch, 'B'));
      await act(async () => pending.resolve(await ok([dir(leaf)])));
      await settle();
      assert.equal(
        fetch.calls.some((c) => c.path === '/api/v1/fs/ls' && c.uri === leaf),
        false,
      );
    } finally {
      await view.unmount();
    }
  });

for (const mode of ['paged', 'error'])
  test(`automatic expansion stops at a ${mode} listing`, async () => {
    const fetch = fixture({}, (call) =>
      call.uri === folder
        ? mode === 'paged'
          ? ok([dir(child)], true)
          : Promise.resolve(new Response('denied', { status: 403 }))
        : undefined,
    );
    const view = await mount(element(fetch));
    try {
      await toggle(view, folder);
      assert.equal(
        fetch.calls.some((c) => c.path === '/api/v1/fs/ls' && c.uri === child),
        false,
      );
    } finally {
      await view.unmount();
    }
  });
