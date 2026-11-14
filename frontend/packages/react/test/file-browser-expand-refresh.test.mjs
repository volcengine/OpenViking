import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createRef } from 'react';
import {
  mount,
  ok,
  recordingFetch,
  deferred,
  settle,
} from './helpers/module-dom.mjs';
import { ROOT, DIR, render, command } from './helpers/file-browser-reveal.mjs';

const BRANCH = `${DIR}/branch`;
const LEAF = `${BRANCH}/README`;
const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);

async function fixture(fileCount = 200, tailCount = 0) {
  let entries = [
    ...Array.from({ length: fileCount }, (_, i) => ({
      uri: `${DIR}/file-${i}`,
    })),
    { uri: BRANCH, isDir: true },
    ...Array.from({ length: tailCount }, (_, i) => ({
      uri: `${DIR}/tail-${i}`,
    })),
  ];
  let nextPage;
  const fetch = recordingFetch(({ path, uri, query }) => {
    if (path === '/api/v1/fs/ls') {
      if (uri === ROOT) return ok([{ uri: DIR, isDir: true }]);
      if (uri === DIR) {
        const offset = Number(query.offset);
        if (offset && nextPage) return nextPage.promise;
        return ok(
          entries.slice(offset, offset + 200),
          offset + 200 < entries.length,
        );
      }
      if (uri === BRANCH) return ok([{ uri: LEAF }]);
    }
    if (path === '/api/v1/fs/stat') return ok({ isDir: uri !== LEAF });
    return ok('');
  });
  const handle = createRef();
  const view = await mount(render(fetch, handle));
  await command(handle, BRANCH);
  return {
    view,
    handle,
    fetch,
    setEntries(value) {
      entries = value;
    },
    pause() {
      nextPage = deferred();
      return nextPage;
    },
    resume() {
      nextPage = undefined;
    },
    entries: () => entries,
  };
}

for (const [fileCount, tailCount, insertions] of [
  [199, 0, 1],
  [200, 0, 1],
  [600, 0, 1],
  [200, 400, 1],
  [200, 400, 201],
]) {
  test(`refresh retains expanded branches (${fileCount + 1} entries, ${tailCount} later entries, ${insertions} inserted)`, async () => {
    const f = await fixture(fileCount, tailCount);
    try {
      const inserted = Array.from({ length: insertions }, (_, i) => ({
        uri: `${DIR}/imported-${i}`,
      }));
      f.setEntries([...inserted, ...f.entries()]);
      const pending = f.pause();
      let opening;
      await act(async () => {
        opening = f.handle.current.openResource(DIR);
      });
      await settle();
      assert.ok(row(f.view, BRANCH));
      assert.ok(row(f.view, LEAF));
      assert.equal(row(f.view, inserted[0].uri), null);
      // Remaining pages use the same service shape; the last cached page may
      // previously have been full, or the loaded window may still have more pages.
      f.resume();
      await act(async () =>
        pending.resolve(
          await ok(f.entries().slice(200, 400), f.entries().length > 400),
        ),
      );
      assert.equal(await opening, true);
      await settle();
      assert.ok(row(f.view, inserted[0].uri));
      assert.equal(row(f.view, BRANCH).getAttribute('aria-expanded'), 'true');
      assert.ok(row(f.view, LEAF));
      assert.equal(
        f.view.container.querySelectorAll('.ov-file-tree__row').length > 200,
        true,
      );
      f.resume();
      f.setEntries([{ uri: `${DIR}/replacement` }]);
      assert.equal(await command(f.handle, DIR), true);
      assert.equal(row(f.view, BRANCH), null);
      assert.ok(row(f.view, `${DIR}/replacement`));
    } finally {
      await f.view.unmount();
    }
  });
}

test('a failed later page retains the old window and selection, and retry restores the whole window', async () => {
  const f = await fixture();
  try {
    const pending = f.pause();
    let opening;
    await act(async () => {
      opening = f.handle.current.openResource(DIR);
    });
    await settle();
    await act(async () =>
      pending.resolve(
        Response.json(
          {
            status: 'error',
            error: { code: 'FAILED', message: 'Refresh unavailable' },
          },
          { status: 503 },
        ),
      ),
    );
    assert.equal(await opening, true);
    await settle();
    assert.equal(row(f.view, DIR).getAttribute('aria-selected'), 'true');
    assert.ok(row(f.view, LEAF));
    const retry = row(f.view, `${DIR}::error`)?.querySelector('button');
    assert.ok(retry);
    f.resume();
    await act(async () => retry.click());
    await settle();
    assert.equal(row(f.view, `${DIR}::error`), null);
    assert.ok(row(f.view, LEAF));
  } finally {
    await f.view.unmount();
  }
});

test('a newer navigation cancels a refresh without publishing its partial pages', async () => {
  const f = await fixture();
  try {
    const previous = f.entries();
    f.setEntries([{ uri: `${DIR}/new` }, ...previous]);
    const pending = f.pause();
    let opening;
    await act(async () => {
      opening = f.handle.current.openResource(DIR);
    });
    await settle();
    assert.equal(await command(f.handle, LEAF), true);
    assert.equal(await opening, false);
    assert.equal(row(f.view, `${DIR}/new`), null);
    assert.equal(row(f.view, LEAF).getAttribute('aria-selected'), 'true');
    assert.equal(
      f.fetch.calls.some((c) => c.path === '/api/v1/fs/ls' && c.uri === LEAF),
      false,
    );
    await act(async () => pending.resolve(await ok(f.entries().slice(200))));
    await settle();
    assert.equal(row(f.view, `${DIR}/new`), null);
    assert.ok(row(f.view, LEAF));
  } finally {
    await f.view.unmount();
  }
});
