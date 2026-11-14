import assert from 'node:assert/strict';
import test from 'node:test';
import {
  failure,
  fixture,
  level,
  mount,
  ok,
  open,
  tree,
} from './helpers/acl-dom.mjs';
import { notificationError } from './helpers/feedback-dom.mjs';

for (const custom of [false, true]) {
  test(`${custom ? 'custom' : 'native'} permission probes consume expected ACL 403 without automatic feedback`, async () => {
    const events = [];
    const fetch = fixture((call) => {
      if (call.path.endsWith('/fs/stat')) return ok({ isDir: true });
      if (call.path.endsWith('/acl')) return failure();
    });
    const acl = custom
      ? {
          async resolvePermission(uri, { client, requestOptions }) {
            try {
              await client.request({
                method: 'GET',
                path: '/api/v1/acl',
                query: { uri },
                options: requestOptions,
              });
              return 'manage';
            } catch (error) {
              if (error.statusCode !== 403) throw error;
            }
          },
        }
      : true;
    const view = await mount(
      tree(
        fetch,
        { acl },
        {
          monitoring: { onEvent: (event) => events.push(event) },
        },
      ),
    );
    try {
      assert.equal(fetch.countOf('/api/v1/acl'), 1);
      assert.equal(Boolean(notificationError()), false);
      assert.equal(
        document.querySelector('.ov-file-browser-acl__lookup-error'),
        null,
      );
      const failures = events.filter(
        (event) => event.name === 'client_request_fail',
      );
      assert.equal(failures.length, 1);
      assert.equal(failures[0].params.httpStatus, 403);
    } finally {
      await view.unmount();
    }
  });
}

test('ACL mutation failures retain one automatic notification and request monitoring', async () => {
  const events = [];
  const fetch = fixture((call) =>
    call.path.endsWith('/acl') && call.init.method === 'PUT'
      ? failure()
      : undefined,
  );
  const view = await mount(
    tree(
      fetch,
      {},
      {
        monitoring: { onEvent: (event) => events.push(event) },
      },
    ),
  );
  try {
    await open();
    await level('group:research', 'Can edit');
    assert.equal(
      document.querySelectorAll('.ov-notification--error').length,
      1,
    );
    assert.match(notificationError().textContent, /Access denied/);
    const failures = events.filter(
      (event) => event.name === 'client_request_fail',
    );
    assert.equal(failures.length, 1);
    assert.equal(failures[0].params.httpStatus, 403);
  } finally {
    await view.unmount();
  }
});
