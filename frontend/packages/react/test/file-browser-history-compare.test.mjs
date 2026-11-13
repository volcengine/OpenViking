import assert from 'node:assert/strict';
import test from 'node:test';
import {
  button,
  click,
  fixture,
  mount,
  ok,
  rootUri,
  tree,
  workflow,
} from './helpers/preview-native-dom.mjs';

const uri = `${rootUri}/notes.md`;
const original = '# Notes\nBefore\n';
const latest = '# Notes\nAfter\n';
const oldOid = 'a'.repeat(40);
const newOid = 'b'.repeat(40);

test('history previews, compares, and copies a revision without writing', async () => {
  const fetch = fixture(uri, false, (call) => {
    if (call.path.endsWith('/snapshot/log'))
      return ok([
        {
          oid: newOid,
          message: 'Latest note',
          author: { time_seconds: 1794268900 },
        },
        {
          oid: oldOid,
          message: 'Earlier note',
          author: { time_seconds: 1794268800 },
        },
      ]);
    if (call.path.endsWith('/snapshot/show'))
      return Promise.resolve(
        new Response(call.query.target_ref === oldOid ? original : latest),
      );
    if (call.path.endsWith('/content/read'))
      return ok(call.query.limit === '-1' ? latest : '');
  });
  const writes = [];
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: {
      async writeText(value) {
        writes.push(value);
      },
    },
  });
  const view = await mount(tree(fetch, uri));
  try {
    await click(view.container.querySelector('[aria-label="Version history"]'));
    const versions = workflow().querySelectorAll('aside li button');
    assert.equal(versions.length, 2);
    assert.match(versions[1].textContent, /Earlier note/);
    await click(versions[1]);
    assert.equal(
      fetch.calls.some(
        (call) =>
          call.path.endsWith('/content/read') && call.query.limit === '-1',
      ),
      false,
    );
    await click(workflow().querySelector('[role="switch"]'));
    assert.match(
      workflow().querySelector('li[data-change="removed"]').textContent,
      /Before/,
    );
    assert.match(
      workflow().querySelector('li[data-change="added"]').textContent,
      /After/,
    );
    assert.equal(fetch.countOf('/api/v1/snapshot/diff'), 0);
    await click(button('Copy source', workflow()));
    assert.deepEqual(writes, [original]);
    assert.equal(button('Restore this version', workflow()), undefined);
    assert.equal(fetch.countOf('/api/v1/snapshot/restore'), 0);
    assert.equal(fetch.countOf('/api/v1/content/write'), 0);
  } finally {
    delete navigator.clipboard;
    await view.unmount();
  }
});
