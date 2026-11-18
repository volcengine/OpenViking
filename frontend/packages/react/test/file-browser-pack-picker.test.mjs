import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixture,
  tree,
  mount,
  click,
  target,
  workflow,
  act,
  ok,
  button,
} from './helpers/preview-native-dom.mjs';

async function selectFile() {
  const input = workflow().querySelector('input[type="file"]');
  const file = new File(['PK\x03\x04archive'], 'resources.ovpack');
  Object.defineProperty(input, 'files', { value: [file] });
  await act(async () =>
    input.dispatchEvent(new window.Event('change', { bubbles: true })),
  );
  assert.equal(button('Import', workflow()).disabled, false);
  return file;
}

const submit = () =>
  act(async () =>
    workflow()
      .querySelector('form')
      .dispatchEvent(
        new window.Event('submit', { bubbles: true, cancelable: true }),
      ),
  );

test('Console pack picker opens at the root and starts each request with an empty selection', async () => {
  const ref = { current: null };
  const view = await mount(tree(fixture(target, true), target, { ref }));
  try {
    let first;
    await act(() => {
      first = ref.current.choosePackFile('viking://');
    });
    await selectFile();

    let second;
    await act(() => {
      second = ref.current.choosePackFile('viking:///');
    });
    assert.equal(await first, undefined);
    const uri = workflow().querySelector('.ov-file-browser-workflow__uri');
    assert.equal(uri.textContent, 'viking://');
    assert.equal(button('Import', workflow()).disabled, true);
    await click(button('Cancel', workflow()));
    assert.equal(await second, undefined);
    assert.equal(workflow(), null);
  } finally {
    await view.unmount();
  }
});

for (const importTarget of ['viking://resources', 'viking://resources/qa']) {
  test(`Console pack picker checks ${importTarget} before selection and before returning the file`, async () => {
    const ref = { current: null };
    const permissionChecks = [];
    let access = 'read';
    const fetch = fixture(target, true, (call) => {
      if (!call.path.endsWith('/fs/stat') || call.uri !== importTarget) return;
      permissionChecks.push(call.uri);
      return ok({ isDir: true, access });
    });
    const view = await mount(tree(fetch, target, { ref, acl: true }));
    try {
      await act(async () => {
        await assert.rejects(
          ref.current.choosePackFile(importTarget),
          /write access/,
        );
      });
      assert.equal(workflow(), null);

      access = 'write';
      let selected;
      await act(() => {
        selected = ref.current.choosePackFile(importTarget);
      });
      await selectFile();
      access = 'read';
      const denied = assert.rejects(selected, /write access/);
      await submit();
      await denied;
      assert.equal(workflow(), null);

      access = 'manage';
      await act(() => {
        selected = ref.current.choosePackFile(importTarget);
      });
      const file = await selectFile();
      await submit();
      assert.equal(await selected, file);
      assert.equal(workflow(), null);
      assert.equal(permissionChecks.length, 5);
    } finally {
      await view.unmount();
    }
  });
}

for (const cancel of [false, true]) {
  test(`Console pack picker locks confirmation during an ACL check (cancel=${cancel})`, async () => {
    const ref = { current: null };
    const importTarget = 'viking://resources/qa';
    let checks = 0;
    let finishCheck;
    const fetch = fixture(target, true, (call) => {
      if (!call.path.endsWith('/fs/stat') || call.uri !== importTarget) return;
      if (++checks === 1) return ok({ isDir: true, access: 'write' });
      return new Promise((resolve) => {
        finishCheck = resolve;
      });
    });
    const view = await mount(tree(fetch, target, { ref, acl: true }));
    try {
      let selected;
      await act(() => {
        selected = ref.current.choosePackFile(importTarget);
      });
      const file = await selectFile();
      await submit();
      assert.equal(
        workflow().querySelector('input[type="file"]').disabled,
        true,
      );
      assert.equal(button('Import', workflow()).disabled, true);
      assert.equal(button('Cancel', workflow()).disabled, false);
      await submit();
      assert.equal(checks, 2);

      if (cancel) await click(button('Cancel', workflow()));
      await act(async () =>
        finishCheck(await ok({ isDir: true, access: 'write' })),
      );
      assert.equal(await selected, cancel ? undefined : file);
      assert.equal(workflow(), null);
    } finally {
      await view.unmount();
    }
  });
}
