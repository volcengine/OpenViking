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
const missingParent = 'e'.repeat(40);
const revisions = () =>
  workflow().querySelectorAll('.ov-file-history__commit-list button');
const badges = () =>
  Array.from(
    workflow().querySelectorAll('.ov-file-history__version-badge'),
    (badge) => badge.textContent,
  );

test('only a confirmed deletion marks the selected history revision', async () => {
  const fetch = fixture(
    uri,
    false,
    (call) => {
      if (call.path.endsWith('/snapshot/versions')) {
        return ok({ versions: [latest, removed, older, missing] });
      }
      if (call.path.endsWith('/snapshot/show')) {
        if (
          [removed.oid, missing.oid, missingParent].includes(
            call.query.target_ref,
          )
        ) {
          return Promise.resolve(
            Response.json(
              {
                status: 'error',
                error: {
                  code: 'NOT_FOUND',
                  message: 'Snapshot content missing',
                  details: { type: 'git_blob', resource: uri },
                },
              },
              { status: 404 },
            ),
          );
        }
        return Promise.resolve(
          new Response(
            call.query.target_ref === older.oid ? 'Older content\n' : '',
          ),
        );
      }
      if (call.path.endsWith('/snapshot/log') && call.query.branch) {
        const commit = call.query.branch === removed.oid ? removed : missing;
        return ok([
          {
            ...commit,
            parents: [commit === removed ? older.oid : missingParent],
          },
        ]);
      }
    },
    [{ to: target }],
  );
  const view = await mount(tree(fetch, uri));
  try {
    await openAction('Version history');
    assert.deepEqual(badges(), []);
    assert.equal(button('Copy source', workflow()).disabled, false);

    await click(revisions()[1]);
    assert.deepEqual(badges(), ['Deleted']);
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

    await click(workflow().querySelector('[role="switch"]'));
    await click(revisions()[3]);
    assert.deepEqual(badges(), []);
    assert.match(
      workflow().querySelector('[role="alert"]').textContent,
      /Snapshot content missing/,
    );
  } finally {
    await view.unmount();
  }
});
