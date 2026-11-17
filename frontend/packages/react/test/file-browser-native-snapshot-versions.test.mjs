import './helpers/history-viewer-dom.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixture,
  tree,
  mount,
  target,
  workflow,
  openAction,
  click,
  button,
  ok,
} from './helpers/preview-native-dom.mjs';

const uri = `${target}/guide.md`;
const commits = ['a', 'b', 'c'].map((character, index) => ({
  oid: character.repeat(40),
  message: `Revision ${3 - index}`,
  author: { time_seconds: 1794260000 - index * 60 },
  committer: { time_seconds: 1794268800 - index * 60 },
}));

test('native snapshot versions use commit IDs and snapshot cursors for preview and restore', async () => {
  const fetch = fixture(
    uri,
    false,
    (call) => {
      if (call.path.endsWith('/snapshot/versions')) {
        assert.equal(call.query.before_version, undefined);
        return ok({
          versions: call.query.before_snapshot
            ? commits.slice(2)
            : commits.slice(0, 2),
          next_before_snapshot: call.query.before_snapshot
            ? null
            : commits[1].oid,
        });
      }
      if (call.path.endsWith('/snapshot/show')) {
        assert.equal(call.query.path, uri);
        return Promise.resolve(new Response(`# ${call.query.target_ref}`));
      }
      if (call.path.endsWith('/snapshot/restore'))
        return ok({ result: 'created' });
    },
    [{ to: target }],
  );
  const view = await mount(tree(fetch, uri));
  try {
    await openAction('Version history');
    const pages = fetch.calls.filter((call) =>
      call.path.endsWith('/snapshot/versions'),
    );
    assert.equal(pages.length, 2);
    assert.equal(pages[1].query.before_snapshot, commits[1].oid);
    await click(button('Markdown', workflow()));
    assert.equal(
      workflow().querySelector('time').dateTime,
      new Date(commits[0].committer.time_seconds * 1000).toISOString(),
    );
    await click(
      [
        ...workflow().querySelectorAll('.ov-file-history__commit-list button'),
      ].at(-1),
    );
    assert.match(workflow().querySelector('pre').textContent, /c{40}/);
    await click(button('Restore this version', workflow()));
    const confirm = [...document.querySelectorAll('[role="dialog"]')].find(
      (node) => node.textContent.includes('Confirm restore'),
    );
    await click(button('Confirm restore', confirm));
    const restore = fetch.calls.find((call) =>
      call.path.endsWith('/snapshot/restore'),
    );
    assert.equal(restore.body.restore_commit_id, commits[2].oid);
    assert.equal(restore.body.current_commit_id, commits[0].oid);
  } finally {
    await view.unmount();
  }
});
