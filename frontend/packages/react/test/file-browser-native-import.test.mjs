import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixture,
  tree,
  mount,
  target,
  openAction,
  workflow,
  act,
  ok,
  settle,
  button,
} from './helpers/preview-native-dom.mjs';
const submit = async () => {
  await act(async () =>
    workflow()
      .querySelector('form')
      .dispatchEvent(
        new window.Event('submit', { bubbles: true, cancelable: true }),
      ),
  );
  await settle();
};

test('import uploads multipart, retains the selected File after failure and reuploads on retry', async () => {
  let upload = 0;
  let writes = 0;
  let resolve;
  const fetch = fixture(target, true, (call) => {
    if (call.path.endsWith('/resources/temp_upload'))
      return ok({ temp_file_id: `upload-${++upload}` });
    if (call.path.endsWith('/pack/import'))
      return ++writes === 1
        ? Promise.resolve(new Response('try again', { status: 503 }))
        : new Promise((done) => {
            resolve = done;
          });
  });
  const view = await mount(tree(fetch, target));
  try {
    await openAction('Import .ovpack');
    const file = new File(['archive'], 'demo.ovpack');
    const input = workflow().querySelector('input[type="file"]');
    Object.defineProperty(input, 'files', {
      configurable: true,
      value: [file],
    });
    await act(async () =>
      input.dispatchEvent(new window.Event('change', { bubbles: true })),
    );
    await submit();
    assert.ok(workflow().querySelector('[role="alert"]'));
    assert.equal(input.files[0], file);
    const count = fetch.calls.length;
    await view.render(tree(fetch, target, { locale: 'zh-CN' }));
    assert.equal(fetch.calls.length, count);
    assert.equal(input.files[0], file);
    await submit();
    await submit();
    assert.equal(writes, 2);
    assert.equal(upload, 2);
    const uploads = fetch.calls.filter((call) =>
      call.path.endsWith('/resources/temp_upload'),
    );
    assert.ok(uploads[0].body instanceof FormData);
    assert.equal(uploads[0].body.get('file').name, 'demo.ovpack');
    assert.equal(
      Object.keys(uploads[0].init.headers).some(
        (key) => key.toLowerCase() === 'content-type',
      ),
      false,
    );
    const imported = fetch.calls.filter((call) =>
      call.path.endsWith('/pack/import'),
    );
    assert.deepEqual(imported[1].body, {
      temp_file_id: 'upload-2',
      parent: target,
    });
    assert.equal(button('导入', workflow()).disabled, true);
    await act(async () => resolve(await ok({ uri: `${target}/imported` })));
    await settle();
    assert.match(
      workflow().querySelector('[role="status"]').textContent,
      /导入成功/,
    );
    assert.equal(workflow().querySelector('[role="alert"]'), null);
  } finally {
    await view.unmount();
  }
});

for (const revoked of [{ readOnly: true }, { acl: true }]) {
  test(`import rechecks current write capability: ${JSON.stringify(revoked)}`, async () => {
    const fetch = fixture(target, true, (call) => {
      if (call.path.endsWith('/fs/stat'))
        return ok({ isDir: true, permission: 'read' });
      if (call.path.endsWith('/resources/temp_upload'))
        return ok({ temp_file_id: 'upload' });
      if (call.path.endsWith('/pack/import')) return ok({ uri: target });
    });
    const view = await mount(tree(fetch, target));
    try {
      await openAction('Import .ovpack');
      const file = new File(['archive'], 'demo.ovpack');
      const input = workflow().querySelector('input[type="file"]');
      Object.defineProperty(input, 'files', {
        configurable: true,
        value: [file],
      });
      await act(async () =>
        input.dispatchEvent(new window.Event('change', { bubbles: true })),
      );
      await view.render(tree(fetch, target, revoked));
      await submit(); // A synthetic submit also exercises the handler rather than only the disabled button.
      assert.equal(
        fetch.calls.filter((call) =>
          /(?:temp_upload|pack\/import)$/.test(call.path),
        ).length,
        0,
      );
      assert.equal(button('Import', workflow()).disabled, true);
      assert.equal(input.files[0], file);
      await view.render(tree(fetch, target));
      assert.equal(button('Import', workflow()).disabled, false);
      await submit();
      assert.equal(
        fetch.calls.filter((call) => /pack\/import$/.test(call.path)).length,
        1,
      );
    } finally {
      await view.unmount();
    }
  });
}
