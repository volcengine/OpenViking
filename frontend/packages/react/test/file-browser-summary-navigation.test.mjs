import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createRef } from 'react';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';
import {
  ROOT,
  DIR,
  FILE,
  scrolled,
  render,
  listing,
  command,
} from './helpers/file-browser-reveal.mjs';

const summaries = /^\/api\/v1\/content\/(abstract|overview)$/;
function response(call) {
  if (summaries.test(call.path)) return ok('');
  if (call.path.endsWith('/content/read')) return ok('');
  return listing(call);
}
function activeLevel(view) {
  return view.container.querySelector(
    '.ov-file-preview__level[aria-selected="true"]',
  );
}
async function selectLevel(view, index) {
  await act(async () => {
    view.container.querySelectorAll('.ov-file-preview__level')[index].click();
  });
  await settle();
}

for (const controlled of [false, true]) {
  test(`summary links reveal the parent and switch levels (${controlled ? 'controlled' : 'uncontrolled'})`, async () => {
    scrolled.length = 0;
    const fetch = recordingFetch(response);
    const handle = createRef();
    const view = await mount(render(fetch, handle, controlled));
    try {
      for (const [filename, label] of [
        ['.overview.md', 'L1'],
        ['.abstract.md', 'L0'],
        ['.overview.md', 'L1'],
      ]) {
        assert.equal(await command(handle, `${DIR}/${filename}`), true);
        assert.match(activeLevel(view)?.textContent ?? '', new RegExp(label));
        assert.equal(
          view.container
            .querySelector('[data-file-tree-row-id][aria-selected="true"]')
            ?.getAttribute('data-file-tree-row-id'),
          DIR,
        );
        assert.equal(scrolled.at(-1), DIR);
      }
      const reads = fetch.calls.filter((call) =>
        summaries.test(call.path),
      ).length;
      await selectLevel(view, 0);
      assert.match(activeLevel(view).textContent, /L0/);
      assert.equal(await command(handle, `${DIR}/.overview.md`), true);
      assert.match(activeLevel(view).textContent, /L1/);
      assert.equal(
        fetch.calls.filter((call) => summaries.test(call.path)).length,
        reads,
      );
      assert.equal(
        fetch.calls.some((call) =>
          /\/\.(abstract|overview)\.md$/.test(call.uri),
        ),
        false,
      );
      assert.equal(await command(handle, FILE), true);
      assert.equal(activeLevel(view), null);
      assert.equal(scrolled.at(-1), FILE);
    } finally {
      await view.unmount();
    }
  });
}

test('preview-only summary links select the level without revealing the tree', async () => {
  scrolled.length = 0;
  const fetch = recordingFetch(response);
  const handle = createRef();
  const view = await mount(render(fetch, handle));
  try {
    const count = fetch.countOf('/api/v1/fs/ls');
    assert.equal(
      await command(handle, `${DIR}/.overview.md`, { reveal: false }),
      true,
    );
    assert.match(activeLevel(view)?.textContent ?? '', /L1/);
    assert.equal(fetch.countOf('/api/v1/fs/ls'), count);
    assert.equal(scrolled.length, 0);
  } finally {
    await view.unmount();
  }
});

test('root summary opens its level while retaining the no-tree-row result', async () => {
  const fetch = recordingFetch((call) =>
    call.path.endsWith('/fs/stat') ? ok({ isDir: true }) : response(call),
  );
  const handle = createRef();
  const view = await mount(render(fetch, handle));
  try {
    for (const uri of [ROOT, 'viking://']) {
      const link = `${uri}${uri.endsWith('/') ? '' : '/'}.overview.md`;
      assert.equal(await command(handle, link), false);
      assert.match(activeLevel(view)?.textContent ?? '', /L1/);
      assert.equal(
        fetch.calls.filter((call) => summaries.test(call.path)).at(-1).uri,
        uri,
      );
    }
  } finally {
    await view.unmount();
  }
});

test('summary retry preserves the requested level after a load failure', async () => {
  let failed = true;
  const fetch = recordingFetch((call) =>
    failed && summaries.test(call.path)
      ? new Response(
          JSON.stringify({ status: 'error', message: 'Summaries unavailable' }),
          { status: 500 },
        )
      : response(call),
  );
  const handle = createRef();
  const view = await mount(render(fetch, handle));
  try {
    assert.equal(await command(handle, `${DIR}/.overview.md`), true);
    assert.match(view.container.textContent, /Failed to load/);
    failed = false;
    await act(async () => {
      [
        ...view.container.querySelectorAll(
          '.ov-file-browser__preview-pane button',
        ),
      ]
        .find((node) => node.textContent === 'Retry')
        .click();
    });
    await settle();
    assert.match(activeLevel(view)?.textContent ?? '', /L1/);
    await selectLevel(view, 0);
    assert.match(activeLevel(view).textContent, /L0/);
  } finally {
    await view.unmount();
  }
});
