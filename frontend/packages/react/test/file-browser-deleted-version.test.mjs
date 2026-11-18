import './helpers/history-viewer-dom.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  button,
  click,
  fixture,
  mount,
  ok,
  openAction,
  settle,
  target,
  tree,
  workflow,
} from './helpers/preview-native-dom.mjs';

const uri = `${target}/guide.md`;
const [latest, removed, older, missing] = ['a', 'b', 'c', 'd'].map(
  (character, index) => ({
    oid: character.repeat(40),
    message: `Revision ${4 - index}`,
    committer: { time_seconds: 1794268800 - index * 60 },
  }),
);
const revisions = () =>
  workflow().querySelectorAll('.ov-file-history__commit-list button');
const badges = () =>
  Array.from(
    workflow().querySelectorAll(
      '.ov-file-history__commit-list .ov-file-history__version-badge',
    ),
    (badge) => badge.textContent,
  );

function replyError(status, error) {
  return Promise.resolve(Response.json({ status: 'error', error }, { status }));
}

function missingContent(details) {
  return replyError(404, {
    code: 'NOT_FOUND',
    message: 'Snapshot content missing',
    details: { type: 'git_blob', resource: uri, ...details },
  });
}

test('history retains confirmed deletion badges and reports missing blobs as read errors', async () => {
  const fetch = fixture(
    uri,
    false,
    (call) => {
      if (call.path.endsWith('/snapshot/versions')) {
        return ok({ versions: [latest, removed, older, missing] });
      }
      if (call.path.endsWith('/snapshot/show')) {
        if (call.query.target_ref === removed.oid)
          return missingContent({
            reason: 'path_not_found',
            deleted_file: true,
          });
        if (call.query.target_ref === missing.oid) return missingContent();
        return Promise.resolve(
          new Response(
            call.query.target_ref === latest.oid
              ? 'Recreated content\n'
              : 'Older content\n',
          ),
        );
      }
      if (call.path.endsWith('/snapshot/log') && call.query.branch) {
        return ok([{ ...missing, parents: [older.oid] }]);
      }
      if (call.path.endsWith('/snapshot/diff'))
        return replyError(404, {
          code: 'NOT_FOUND',
          message: 'Tracked blob is missing',
        });
    },
    [{ to: target }],
  );
  const view = await mount(tree(fetch, uri));
  try {
    await openAction('Version history');
    assert.deepEqual(badges(), []);
    assert.equal(button('Copy source', workflow()).disabled, false);

    await click(workflow().querySelector('[role="switch"]'));
    assert.match(
      workflow().querySelector('pre').textContent,
      /\+Recreated content/,
    );
    assert.deepEqual(badges(), ['Deleted']);
    await click(workflow().querySelector('[aria-label="Compare with"]'));
    assert.ok(
      [...document.querySelectorAll('[role="option"]')].some((option) =>
        option.textContent.includes('Deleted'),
      ),
    );
    await click(workflow().querySelector('[aria-label="Compare with"]'));
    await click(workflow().querySelector('[role="switch"]'));
    assert.deepEqual(badges(), ['Deleted']);

    await click(revisions()[1]);
    assert.deepEqual(badges(), ['Deleted']);
    assert.equal(fetch.countOf('/api/v1/snapshot/log'), 0);
    assert.equal(fetch.countOf('/api/v1/snapshot/diff'), 0);
    assert.equal(
      fetch.calls.some((call) => call.query.target_ref === older.oid),
      false,
    );
    assert.match(revisions()[1].textContent, /Deleted/);
    assert.match(
      workflow().textContent,
      /The file was deleted in this version/,
    );
    assert.equal(button('Copy source', workflow()).disabled, true);
    assert.equal(button('Restore this version', workflow()).disabled, true);

    await click(workflow().querySelector('[role="switch"]'));
    await settle();
    assert.match(
      workflow().querySelector('pre').textContent,
      /\+\+\+ \/dev\/null/,
    );
    assert.match(workflow().querySelector('pre').textContent, /-Older content/);

    await click(revisions()[0]);
    assert.deepEqual(badges(), ['Deleted']);
    await click(revisions()[3]);
    assert.deepEqual(badges(), ['Deleted']);
    assert.doesNotMatch(revisions()[3].textContent, /Deleted/);
    assert.equal(workflow().querySelector('pre'), null);
    assert.match(
      workflow().querySelector('[role="alert"]').textContent,
      /Snapshot content missing/,
    );
    assert.equal(fetch.countOf('/api/v1/snapshot/diff'), 1);
  } finally {
    await view.unmount();
  }
});

for (const [label, response, message] of [
  [
    'the first-parent path was a directory',
    () => missingContent({ reason: 'path_not_found', deleted_file: false }),
    /Snapshot content missing/,
  ],
  [
    'the deletion marker is a string',
    () => missingContent({ reason: 'path_not_found', deleted_file: 'true' }),
    /Snapshot content missing/,
  ],
  [
    'the deletion marker is null',
    () => missingContent({ reason: 'path_not_found', deleted_file: null }),
    /Snapshot content missing/,
  ],
  [
    'the error declares another reason',
    () => missingContent({ reason: 'object_not_found', deleted_file: true }),
    /Snapshot content missing/,
  ],
  [
    'the typed check was denied',
    () =>
      replyError(403, {
        code: 'PERMISSION_DENIED',
        message: 'Cannot read snapshot history',
      }),
    /Cannot read snapshot history/,
  ],
  [
    'the typed check timed out',
    () =>
      replyError(504, {
        code: 'DEADLINE_EXCEEDED',
        message: 'Snapshot tree lookup timed out',
      }),
    /Snapshot tree lookup timed out/,
  ],
]) {
  test(`typed history stays a read error when ${label}`, async () => {
    const fetch = fixture(
      uri,
      false,
      (call) => {
        if (call.path.endsWith('/snapshot/versions'))
          return ok({ versions: [removed, older] });
        if (call.path.endsWith('/snapshot/show')) return response();
      },
      [{ to: target }],
    );
    const view = await mount(tree(fetch, uri));
    try {
      await openAction('Version history');
      assert.deepEqual(badges(), []);
      assert.match(
        workflow().querySelector('[role="alert"]').textContent,
        message,
      );
      assert.equal(fetch.countOf('/api/v1/snapshot/log'), 0);
      assert.equal(fetch.countOf('/api/v1/snapshot/diff'), 0);
    } finally {
      await view.unmount();
    }
  });
}
