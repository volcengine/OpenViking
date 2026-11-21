import './helpers/history-viewer-dom.mjs';
import { notificationContent } from './helpers/feedback-dom.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixture,
  tree,
  mount,
  target,
  openAction,
  workflow,
  click,
  button,
  ok,
  settle,
} from './helpers/preview-native-dom.mjs';

for (const directory of [true, false]) {
  test(`${directory ? 'directory' : 'file'} history retains the selected version and re-confirms after a restore conflict`, async () => {
    const uri = `${target}/doc.md`;
    let blocked = true;
    let latest = 2;
    const fetch = fixture(
      directory ? target : uri,
      directory,
      (call) => {
        if (call.path.endsWith('/fs/ls') && call.uri === target)
          return ok([{ uri, isDir: false }]);
        if (call.path.endsWith('/snapshot/versions'))
          return ok({
            versions: Array.from({ length: latest }, (_, i) => latest - i).map(
              (n) => ({
                version_id: `v${n}`,
                version_number: n,
                snapshot_id: `commit${n}`,
                snapshot_path: uri,
                created_at: 1794268800 + n,
              }),
            ),
          });
        if (call.path.endsWith('/snapshot/show'))
          return Promise.resolve(
            new Response(
              call.query.target_ref === 'commit1' ? 'old body' : 'new body',
            ),
          );
        if (call.path.endsWith('/snapshot/restore')) {
          if (blocked) latest = 3;
          return blocked
            ? Promise.resolve(
                Response.json(
                  {
                    status: 'error',
                    error: { code: 'CONFLICT', message: 'File changed' },
                  },
                  { status: 409 },
                ),
              )
            : ok({ result: directory ? 'noop' : 'created' });
        }
      },
      [{ to: target }],
    );
    const view = await mount(tree(fetch, directory ? target : uri));
    try {
      await openAction('Version history');
      await settle(10);
      if (directory)
        assert.equal(
          workflow().querySelector('[aria-label="Select file"] input')?.value ??
            workflow().querySelector('input[name="history-file"]')?.value,
          uri,
        );
      assert.match(workflow().textContent, /new body/);
      if (directory) assert.match(workflow().textContent, /old body/);
      if (directory) {
        await click(
          workflow().querySelector('[aria-label="Select historical version"]'),
        );
        await click([...document.querySelectorAll('[role="option"]')].at(-1));
      } else {
        await click(workflow().querySelector('[aria-label="Version 1"]'));
      }
      await click(button('Restore this version', workflow()));
      const confirmation = () =>
        [...document.querySelectorAll('[role="dialog"]')].find((node) =>
          node.textContent.includes('Restore this document to this version?'),
        );
      await click(button('Confirm restore', confirmation()));
      assert.match(notificationContent()?.textContent ?? '', /File changed/);
      assert.ok(confirmation());
      assert.equal(fetch.countOf('/api/v1/snapshot/versions'), 2);
      assert.equal(fetch.countOf('/api/v1/snapshot/restore'), 1);
      blocked = false;
      if (!directory) {
        await click(button('Cancel', confirmation()));
        await click(button('Restore this version', workflow()));
      }
      await click(button('Confirm restore', confirmation()));
      assert.equal(confirmation(), undefined);
      const success = () =>
        [...document.querySelectorAll('.ov-toast--success')].map(
          (node) => node.textContent,
        );
      assert.deepEqual(success(), [
        directory
          ? 'The selected version matches the current file. No new version was created.'
          : 'Restore completed.',
      ]);
      const writes = fetch.calls.filter((call) =>
        call.path.endsWith('/snapshot/restore'),
      );
      assert.equal(writes.length, 2);
      assert.deepEqual(writes[1].body, {
        path: uri,
        restore_commit_id: 'commit1',
        current_commit_id: 'commit3',
      });
      assert.equal(
        fetch.calls.some((call) => call.path.endsWith('/snapshot/commit')),
        false,
      );
    } finally {
      await view.unmount();
    }
  });
}
