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
  settle,
} from './helpers/preview-native-dom.mjs';

const uri = `${target}/guide.md`;
const commits = ['a', 'b', 'c'].map((character, index) => ({
  oid: character.repeat(40),
  message: `Revision ${3 - index}`,
  author: { time_seconds: 1794260000 - index * 60 },
  committer: { time_seconds: 1794268800 - index * 60 },
}));

test('native snapshot versions use commit IDs and snapshot cursors for preview and restore', async () => {
  const restored = {
    oid: 'd'.repeat(40),
    message: `restore ${uri} to ${commits[2].oid}`,
    committer: { time_seconds: 1794270000 },
  };
  let versions = commits;
  const fetch = fixture(
    uri,
    false,
    (call) => {
      if (call.path.endsWith('/snapshot/versions')) {
        assert.equal(call.query.before_version, undefined);
        const start = call.query.before_snapshot
          ? versions.findIndex(
              ({ oid }) => oid === call.query.before_snapshot,
            ) + 1
          : 0;
        const page = versions.slice(start, start + 2);
        return ok({
          uri,
          versions: page,
          next_before_snapshot:
            start + page.length < versions.length ? page.at(-1).oid : null,
        });
      }
      if (call.path.endsWith('/snapshot/show')) {
        assert.equal(call.query.path, uri);
        return Promise.resolve(new Response(`# ${call.query.target_ref}`));
      }
      if (call.path.endsWith('/snapshot/restore')) {
        versions = [restored, ...commits];
        return ok({ result: 'created' });
      }
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
    await settle();
    const badges = workflow().querySelectorAll(
      '.ov-file-history__commit-list .ov-file-history__version-badge',
    );
    assert.deepEqual(
      Array.from(badges, (badge) => badge.textContent),
      ['Restored'],
    );
    assert.equal(
      badges[0].closest('button').querySelector('time').dateTime,
      new Date(restored.committer.time_seconds * 1000).toISOString(),
    );
    assert.equal(badges[0].previousElementSibling.textContent, 'Latest');
  } finally {
    await view.unmount();
  }
});
