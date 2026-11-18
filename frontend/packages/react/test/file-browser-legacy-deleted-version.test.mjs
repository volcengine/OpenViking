import './helpers/history-viewer-dom.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixture,
  mount,
  ok,
  openAction,
  target,
  tree,
  workflow,
} from './helpers/preview-native-dom.mjs';

const uri = `${target}/guide.md`;
const removed = { oid: 'b'.repeat(40), message: 'Removed file' };
const older = { oid: 'c'.repeat(40), message: 'Previous file version' };
const secondParent = 'd'.repeat(40);

function replyError(status, error) {
  return Promise.resolve(Response.json({ status: 'error', error }, { status }));
}

const deletedDiff = {
  change_type: 'deleted',
  from_commit: older.oid,
  to_commit: removed.oid,
};

for (const [label, compare, message, commit = removed] of [
  ['explicit deletion', () => ok(deletedDiff)],
  [
    'a mismatched comparison result',
    () => ok({ ...deletedDiff, from_commit: secondParent }),
    /Snapshot content missing/,
  ],
  [
    'no diff endpoint',
    () => replyError(404, { code: 'NOT_FOUND', message: 'Route unavailable' }),
    /Snapshot content missing/,
  ],
  ['no exact path change', undefined, /Snapshot content missing/, older],
  [
    'a diff authorization failure',
    () =>
      replyError(403, {
        code: 'PERMISSION_DENIED',
        message: 'Cannot read snapshot history',
      }),
    /Cannot read snapshot history/,
  ],
  [
    'a diff timeout',
    () =>
      replyError(504, {
        code: 'DEADLINE_EXCEEDED',
        message: 'Snapshot diff timed out',
      }),
    /Snapshot diff timed out/,
  ],
  [
    'a directory parent',
    () =>
      replyError(400, {
        code: 'INVALID_ARGUMENT',
        message: 'Snapshot path is a directory',
      }),
    /Snapshot path is a directory/,
  ],
]) {
  test(`legacy history handles ${label}`, async () => {
    const fetch = fixture(
      uri,
      false,
      (call) => {
        if (call.path.endsWith('/snapshot/versions'))
          return replyError(404, {
            code: 'NOT_FOUND',
            message: 'Route unavailable',
          });
        if (call.path.endsWith('/snapshot/log')) {
          if (!call.query.branch) return ok([removed, older]);
          assert.equal(call.query.branch, removed.oid);
          assert.equal(call.query.paths, uri);
          assert.equal(call.query.limit, '1');
          return ok([{ ...commit, parents: [older.oid, secondParent] }]);
        }
        if (call.path.endsWith('/snapshot/show')) {
          return replyError(404, {
            code: 'NOT_FOUND',
            message: 'Snapshot content missing',
            details: { type: 'git_blob', resource: uri },
          });
        }
        if (call.path.endsWith('/snapshot/diff')) {
          assert.equal(call.query.path, uri);
          assert.equal(call.query.from, older.oid);
          assert.equal(call.query.to, removed.oid);
          return compare();
        }
      },
      [{ to: target }],
    );
    const view = await mount(tree(fetch, uri));
    try {
      await openAction('Version history');
      assert.equal(fetch.countOf('/api/v1/snapshot/show'), 1);
      assert.equal(fetch.countOf('/api/v1/snapshot/log'), 2);
      assert.equal(fetch.countOf('/api/v1/snapshot/diff'), compare ? 1 : 0);
      const badge = workflow().querySelector('.ov-file-history__version-badge');
      if (message) {
        assert.equal(badge, null);
        assert.match(
          workflow().querySelector('[role="alert"]').textContent,
          message,
        );
      } else {
        assert.equal(badge?.textContent, 'Deleted');
      }
    } finally {
      await view.unmount();
    }
  });
}
