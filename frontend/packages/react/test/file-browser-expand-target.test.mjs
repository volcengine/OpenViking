import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h, createRef } from 'react';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { OpenVikingProvider } from '@openviking/react/client';
import { mount, ok, recordingFetch } from './helpers/module-dom.mjs';
import {
  ROOT,
  DIR,
  FILE,
  render,
  listing,
  command,
  scrolled,
} from './helpers/file-browser-reveal.mjs';

const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);

for (const controlled of [false, true]) {
  test(`directory navigation expands the target (${controlled ? 'controlled' : 'uncontrolled'})`, async () => {
    const fetch = recordingFetch(listing);
    const handle = createRef();
    const view = await mount(render(fetch, handle, controlled));
    try {
      assert.equal(await command(handle, DIR), true);
      assert.equal(row(view, DIR).getAttribute('aria-expanded'), 'true');
      assert.equal(row(view, DIR).getAttribute('aria-selected'), 'true');
      assert.ok(row(view, `${DIR}/first.png`));
      assert.equal(await command(handle, FILE), true);
      assert.equal(row(view, FILE).getAttribute('aria-selected'), 'true');
      assert.equal(
        fetch.calls.some((c) => c.path === '/api/v1/fs/ls' && c.uri === FILE),
        false,
      );
      const count = fetch.countOf('/api/v1/fs/ls');
      assert.equal(await command(handle, DIR, { reveal: false }), true);
      assert.equal(fetch.countOf('/api/v1/fs/ls'), count);
    } finally {
      await view.unmount();
    }
  });

  test(`root expansion refreshes and scrolls its container (${controlled ? 'controlled' : 'uncontrolled'})`, async () => {
    scrolled.length = 0;
    const fetch = recordingFetch(listing);
    const handle = createRef();
    const view = await mount(render(fetch, handle, controlled));
    try {
      const scroll = view.container.querySelector(
        '.ov-file-browser__tree-scroll [role="tree"]',
      );
      const before = fetch.calls.filter(
        (c) => c.path === '/api/v1/fs/ls' && c.uri === ROOT,
      ).length;
      scroll.scrollTop = 120;
      assert.equal(await command(handle, ROOT, { reveal: false }), true);
      assert.equal(scroll.scrollTop, 120);
      assert.equal(await command(handle, ROOT), true);
      assert.equal(scroll.scrollTop, 0);
      assert.equal(row(view, ROOT), null);
      assert.deepEqual(scrolled, []);
      assert.equal(
        fetch.calls.filter((c) => c.path === '/api/v1/fs/ls' && c.uri === ROOT)
          .length,
        before + 1,
      );
    } finally {
      await view.unmount();
    }
  });
}

for (const accepts of [false, true]) {
  test(`an empty root only succeeds when the controlled host accepts selection (${accepts})`, async () => {
    const handle = createRef();
    const fetch = recordingFetch(({ path }) =>
      path === '/api/v1/fs/ls' ? ok([]) : ok(''),
    );
    const view = await mount(
      h(
        OpenVikingProvider,
        { fetch },
        h(FileBrowser, {
          ref: handle,
          rootUri: ROOT,
          uri: accepts ? ROOT : FILE,
          dashboardKinds: [],
        }),
      ),
    );
    try {
      assert.equal(await command(handle, ROOT), accepts);
      assert.equal(row(view, ROOT), null);
    } finally {
      await view.unmount();
    }
  });
}

test('a canonical root target refreshes an alias root without inventing a row', async () => {
  const alias = 'viking://~/resources';
  const canonical = 'viking://user/default/resources';
  const handle = createRef();
  const fetch = recordingFetch(({ path, uri }) => {
    if (path === '/api/v1/fs/ls') return ok([]);
    if (path === '/api/v1/fs/stat' && uri === 'viking://~')
      return ok({ uri: 'viking://user/default', isDir: true });
    return ok('');
  });
  const view = await mount(
    h(
      OpenVikingProvider,
      { fetch },
      h(FileBrowser, {
        ref: handle,
        rootUri: alias,
        dashboardKinds: [],
      }),
    ),
  );
  try {
    assert.equal(await command(handle, `${canonical}/`), true);
    assert.deepEqual(
      fetch.calls.filter((c) => c.path === '/api/v1/fs/ls').map((c) => c.uri),
      [alias, alias],
    );
  } finally {
    await view.unmount();
  }
});

test('a root child refresh failure retains navigation and the existing retry action', async () => {
  let failing = false;
  const fetch = recordingFetch((call) => {
    if (failing && call.path === '/api/v1/fs/ls')
      return Response.json(
        {
          status: 'error',
          error: { code: 'FAILED', message: 'Root unavailable' },
        },
        { status: 503 },
      );
    if (call.path === '/api/v1/fs/stat') return ok({ isDir: true });
    if (call.path.startsWith('/api/v1/content/')) return ok('');
    return listing(call);
  });
  const handle = createRef();
  const view = await mount(render(fetch, handle));
  try {
    failing = true;
    assert.equal(await command(handle, ROOT), true);
    assert.ok(row(view, `${ROOT}/first.png`));
    assert.ok(row(view, `${ROOT}::error`).querySelector('button'));
    assert.equal(
      view.container.querySelector(
        '[data-file-tree-row-id][aria-selected="true"]',
      ),
      null,
    );
  } finally {
    await view.unmount();
  }
});
