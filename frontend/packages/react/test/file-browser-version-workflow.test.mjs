import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import {
  button,
  click,
  fixture,
  mount,
  ok,
  rootUri,
  settle,
  tree,
  workflow,
} from './helpers/preview-native-dom.mjs';
import { setTextarea } from './helpers/module-dom.mjs';

const uri = `${rootUri}/notes.md`;
const original = '# Notes\nBefore\n';
const latest = '# Notes\nAfter\n';
const oldOid = 'a'.repeat(40);
const newOid = 'b'.repeat(40);
const digest = (body) => createHash('sha256').update(body).digest('hex');

test('versioned editing confirms a note, keeps the draft on conflict, and sends the original digest', async () => {
  let fail = true;
  const fetch = fixture(uri, false, (call) => {
    if (call.path.endsWith('/content/read'))
      return ok(call.query.limit === '-1' ? original : '');
    if (call.path.endsWith('/content/write')) {
      return fail
        ? Promise.resolve(
            Response.json(
              {
                status: 'error',
                error: { code: 'CONFLICT', message: 'File changed' },
              },
              { status: 409 },
            ),
          )
        : ok({ snapshot: { commit_oid: newOid } });
    }
  });
  const view = await mount(tree(fetch, uri));
  const note = () =>
    document.querySelector('.ov-file-preview-version-note [role="dialog"]');
  try {
    await click(view.container.querySelector('[aria-label="Edit"]'));
    const draft = view.container.querySelector('textarea');
    assert.equal(draft.value, original);
    await setTextarea(draft, latest);
    await click(button('Save', view.container));
    assert.ok(note());
    assert.equal(fetch.countOf('/api/v1/content/write'), 0);
    await click(button('Cancel', note()));
    assert.equal(view.container.querySelector('textarea').value, latest);
    await click(button('Save', view.container));
    await setTextarea(note().querySelector('textarea'), 'Improve notes');
    await click(button('Confirm', note()));
    assert.match(
      note().querySelector('[role="alert"]').textContent,
      /Failed to save/,
    );
    assert.equal(view.container.querySelector('textarea').value, latest);
    assert.equal(note().querySelector('textarea').value, 'Improve notes');
    assert.deepEqual(
      fetch.calls.find((call) => call.path.endsWith('/content/write')).body,
      {
        uri,
        content: latest,
        mode: 'replace',
        edit_source: 'manual',
        version_message: 'Improve notes',
        expected_content_sha256: digest(original),
      },
    );
    fail = false;
    await click(button('Confirm', note()));
    await settle();
    assert.equal(note(), null);
    assert.equal(view.container.querySelector('textarea'), null);
  } finally {
    await view.unmount();
  }
});

test('history compares a selected version to the live file, copies it, and confirms single-file restore', async () => {
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
    if (call.path.endsWith('/snapshot/restore'))
      return ok({ snapshot: { commit_oid: 'c'.repeat(40) } });
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
    await click(button('Restore this version', workflow()));
    assert.equal(fetch.countOf('/api/v1/snapshot/restore'), 0);
    await click(
      button(
        'Restore this version',
        workflow().querySelector('.ov-file-history__confirm'),
      ),
    );
    assert.deepEqual(
      fetch.calls.find((call) => call.path.endsWith('/snapshot/restore')).body,
      {
        path: uri,
        restore_commit_id: oldOid,
        current_commit_id: newOid,
        expected_content_sha256: digest(latest),
      },
    );
    assert.equal(workflow(), null);
  } finally {
    delete navigator.clipboard;
    await view.unmount();
  }
});
