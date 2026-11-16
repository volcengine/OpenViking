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

test('directory history selects real files, compares historical versions and retries a conflicted restore', async () => {
  const uri = `${target}/doc.md`;
  let blocked = true;
  const fetch = fixture(
    target,
    true,
    (call) => {
      if (call.path.endsWith('/fs/ls') && call.uri === target)
        return ok([{ uri, isDir: false }]);
      if (call.path.endsWith('/snapshot/versions'))
        return ok({
          versions: [2, 1].map((n) => ({
            version_id: `v${n}`,
            version_number: n,
            snapshot_id: `commit${n}`,
            snapshot_path: uri,
            created_at: 1794268800 + n,
          })),
        });
      if (call.path.endsWith('/snapshot/show'))
        return Promise.resolve(
          new Response(
            call.query.target_ref === 'commit1' ? 'old body' : 'new body',
          ),
        );
      if (call.path.endsWith('/snapshot/restore'))
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
          : ok({ result: 'noop' });
    },
    [{ to: target }],
  );
  const view = await mount(tree(fetch, target));
  try {
    await openAction('Version history');
    await settle(10);
    assert.equal(
      workflow().querySelector('[aria-label="Select file"] input')?.value ??
        workflow().querySelector('input[name="history-file"]')?.value,
      uri,
    );
    assert.match(workflow().textContent, /new body/);
    assert.match(workflow().textContent, /old body/);
    await click(
      workflow().querySelector('[aria-label="Select historical version"]'),
    );
    await click([...document.querySelectorAll('[role="option"]')].at(-1));
    await click(button('Restore this version', workflow()));
    const confirmation = () =>
      [...document.querySelectorAll('[role="dialog"]')].find((node) =>
        node.textContent.includes('Restore this document to this version?'),
      );
    await click(button('Confirm restore', confirmation()));
    assert.match(notificationContent()?.textContent ?? '', /File changed/);
    blocked = false;
    await click(button('Confirm restore', confirmation()));
    assert.equal(confirmation(), undefined);
    assert.match(workflow().textContent, /no new version was created/);
    const writes = fetch.calls.filter((call) =>
      call.path.endsWith('/snapshot/restore'),
    );
    assert.equal(writes.length, 2);
    assert.deepEqual(writes[1].body, {
      path: uri,
      restore_commit_id: 'commit1',
      current_commit_id: 'commit2',
    });
    assert.equal(
      fetch.calls.some((call) => call.path.endsWith('/snapshot/commit')),
      false,
    );
  } finally {
    await view.unmount();
  }
});
