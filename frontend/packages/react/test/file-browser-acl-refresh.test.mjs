import assert from 'node:assert/strict';
import test from 'node:test';
import { deferred } from './helpers/module-dom.mjs';
import {
  act,
  click,
  failure,
  fixture,
  mount,
  ok,
  settle,
  tree,
  URI,
} from './helpers/acl-dom.mjs';

const file = `${URI}/guide.md`;
function fileFixture(permission) {
  return fixture((call) => {
    if (call.path.endsWith('/fs/stat') && call.uri === file)
      return permission(call);
    if (call.path.endsWith('/fs/ls') && call.uri === URI)
      return ok([{ uri: file, isDir: false }]);
    if (call.path.endsWith('/content/read')) return ok('');
  });
}

for (const [before, after, label] of [
  ['read', 'write', 'Can edit'],
  ['write', 'read', 'Can read'],
  ['write', 'denied', null],
]) {
  test(`tree refresh reloads the selected file permission: ${before} to ${after}`, async () => {
    let permission = before;
    const fetch = fileFixture(() =>
      permission === 'denied'
        ? failure()
        : ok({ isDir: false, access: permission }),
    );
    const view = await mount(
      tree(fetch, { defaultUri: file, defaultExpandedUri: URI }),
    );
    try {
      assert.equal(
        Boolean(view.container.querySelector('button[aria-label="Edit"]')),
        before === 'write',
      );
      permission = after;
      await click(view.container.querySelector('.ov-file-browser__refresh'));
      assert.equal(
        document.querySelector('.ov-file-browser-acl__tag')?.textContent ??
          null,
        label,
      );
      assert.equal(
        Boolean(view.container.querySelector('button[aria-label="Edit"]')),
        after === 'write',
      );
    } finally {
      await view.unmount();
    }
  });
}

test('a later refresh ignores the previous permission response for the same file', async () => {
  let phase = 0;
  const pending = [];
  const fetch = fileFixture((call) => {
    if (phase === 1) {
      const response = deferred();
      pending.push({ response, signal: call.init.signal });
      return response.promise;
    }
    return ok({ isDir: false, access: phase === 0 ? 'write' : 'read' });
  });
  const view = await mount(
    tree(fetch, { defaultUri: file, defaultExpandedUri: URI }),
  );
  try {
    phase = 1;
    await click(view.container.querySelector('.ov-file-browser__refresh'));
    assert.ok(pending.length > 0);
    phase = 2;
    await click(view.container.querySelector('.ov-file-browser__refresh'));
    assert.equal(
      document.querySelector('.ov-file-browser-acl__tag')?.textContent,
      'Can read',
    );
    assert.ok(pending.some(({ signal }) => signal.aborted));
    await act(async () => {
      for (const { response } of pending)
        response.resolve(await ok({ isDir: false, access: 'write' }));
    });
    await settle();
    assert.equal(
      document.querySelector('.ov-file-browser-acl__tag')?.textContent,
      'Can read',
    );
    assert.equal(
      view.container.querySelector('button[aria-label="Edit"]'),
      null,
    );
  } finally {
    await view.unmount();
  }
});

test('refresh cancels an older manager probe before it can restore access', async () => {
  let phase = 0;
  let signal;
  const pending = deferred();
  const resolvePermission = async (_uri, context) => {
    if (phase === 1) {
      signal = context.requestOptions.signal;
      return pending.promise;
    }
    return phase === 0 ? 'manage' : 'read';
  };
  const view = await mount(tree(fixture(), { acl: { resolvePermission } }));
  try {
    await click(document.querySelector('.ov-file-browser-acl__tag'));
    phase = 1;
    await click(document.querySelector('.ov-file-browser-acl__manage'));
    assert.ok(signal);
    phase = 2;
    await click(view.container.querySelector('.ov-file-browser__refresh'));
    assert.equal(
      document.querySelector('.ov-file-browser-acl__tag')?.textContent,
      'Can read',
    );
    assert.equal(signal.aborted, true);
    await act(async () => pending.resolve('manage'));
    await settle();
    assert.equal(
      document.querySelector('.ov-file-browser-acl__tag')?.textContent,
      'Can read',
    );
    assert.equal(
      document.querySelector('.ov-file-browser-acl [role="dialog"]'),
      null,
    );
  } finally {
    await view.unmount();
  }
});
