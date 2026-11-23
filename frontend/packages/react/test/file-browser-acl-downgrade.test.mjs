import assert from 'node:assert/strict';
import test from 'node:test';
import { failure, fixture, mount, ok, open, tree } from './helpers/acl-dom.mjs';

for (const duringLoad of [false, true]) {
  test(`permission downgrade ${duringLoad ? 'during ACL loading' : 'before opening'} removes management`, async () => {
    let downgraded = false;
    let denied = false;
    const fetch = fixture((call) => {
      if (call.path.endsWith('/fs/stat'))
        return ok({
          isDir: true,
          access: downgraded && (!duringLoad || denied) ? 'read' : 'manage',
        });
      if (call.path.endsWith('/acl') && downgraded) {
        denied = true;
        return failure();
      }
    });
    const view = await mount(tree(fetch));
    try {
      assert.match(
        document.querySelector('.ov-file-browser-acl__tag').textContent,
        /manage/i,
      );
      downgraded = true;
      await open();
      assert.equal(
        document.querySelector('.ov-file-browser-acl [role="dialog"]'),
        null,
      );
      assert.match(document.body.textContent, /permissions have changed/);
      if (!duringLoad) {
        assert.match(
          document.querySelector('.ov-file-browser-acl__tag').textContent,
          /read/i,
        );
        assert.equal(fetch.countOf('/api/v1/acl'), 0);
      }
    } finally {
      await view.unmount();
    }
  });
}

test('temporary ACL load failure retains retry and the management dialog', async () => {
  let fail = true;
  const fetch = fixture((call) => {
    if (call.path.endsWith('/acl') && fail)
      return new Response(
        JSON.stringify({
          status: 'error',
          error: { code: 'INTERNAL', message: 'Temporary failure' },
        }),
        { status: 500 },
      );
  });
  const view = await mount(tree(fetch));
  try {
    await open();
    assert.ok(document.querySelector('.ov-file-browser-acl [role="dialog"]'));
    const retry = [...document.querySelectorAll('button')].find(
      (node) => node.textContent.trim() === 'Retry',
    );
    assert.ok(retry);
    fail = false;
    const { click } = await import('./helpers/acl-dom.mjs');
    await click(retry);
    assert.ok(document.querySelector('.ov-file-browser-acl__entry'));
  } finally {
    await view.unmount();
  }
});
