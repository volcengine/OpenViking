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

function missingContent(reason) {
  return replyError(404, {
    code: 'NOT_FOUND',
    message: 'Snapshot content missing',
    details: { type: 'git_blob', resource: uri, reason },
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
          return missingContent('path_not_found');
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
        assert.equal(call.query.branch, removed.oid);
        assert.equal(call.query.paths, uri);
        assert.equal(call.query.limit, '1');
        return ok([{ ...removed, parents: [older.oid] }]);
      }
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
    assert.equal(fetch.countOf('/api/v1/snapshot/diff'), 0);
    assert.ok(
      !fetch.calls.some(
        (call) =>
          call.path.endsWith('/snapshot/show') &&
          call.query.target_ref === older.oid,
      ),
      'deletion confirmation needs no content from the parent',
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
  } finally {
    await view.unmount();
  }
});

for (const [label, confirmation, message] of [
  ['a matching first parent', () => ok([{ ...removed, parents: [older.oid] }])],
  [
    'an older matching commit',
    () => ok([{ ...older, parents: [missing.oid] }]),
    /Snapshot content missing/,
  ],
  [
    'no first parent',
    () => ok([{ ...removed, parents: [] }]),
    /Snapshot content missing/,
  ],
  [
    'an authorization failure',
    () =>
      replyError(403, {
        code: 'PERMISSION_DENIED',
        message: 'Cannot read snapshot history',
      }),
    /Cannot read snapshot history/,
  ],
  [
    'a timeout',
    () =>
      replyError(504, {
        code: 'DEADLINE_EXCEEDED',
        message: 'Snapshot log timed out',
      }),
    /Snapshot log timed out/,
  ],
]) {
  test(`log and show gateway handles ${label}`, async () => {
    const fetch = fixture(
      uri,
      false,
      (call) => {
        if (call.path.endsWith('/snapshot/versions'))
          return replyError(404, {
            code: 'NOT_FOUND',
            message: 'Route unavailable',
          });
        if (call.path.endsWith('/snapshot/log'))
          return call.query.branch ? confirmation() : ok([removed, older]);
        if (call.path.endsWith('/snapshot/show'))
          return missingContent('path_not_found');
      },
      [{ to: target }],
    );
    const view = await mount(tree(fetch, uri));
    try {
      await openAction('Version history');
      assert.equal(fetch.countOf('/api/v1/snapshot/show'), 1);
      assert.equal(fetch.countOf('/api/v1/snapshot/log'), 2);
      assert.equal(fetch.countOf('/api/v1/snapshot/diff'), 0);
      if (message) {
        assert.deepEqual(badges(), []);
        assert.match(
          workflow().querySelector('[role="alert"]').textContent,
          message,
        );
      } else {
        assert.deepEqual(badges(), ['Deleted']);
      }
    } finally {
      await view.unmount();
    }
  });
}
