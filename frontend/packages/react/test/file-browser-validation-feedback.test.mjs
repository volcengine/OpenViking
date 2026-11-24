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
  click,
} from './helpers/preview-native-dom.mjs';

test('compile required-field validation uses one Toast without making a write request', async () => {
  const fetch = fixture(
    target,
    true,
    (call) =>
      call.path.endsWith('/compile/config')
        ? ok({ to: target, from: [], skill: '', auto_update: false })
        : undefined,
    [{ to: target }],
  );
  const view = await mount(tree(fetch, target));
  try {
    await openAction('Compile configuration');
    await click(document.querySelector('[aria-label="Update schedule"]'));
    const edit = () =>
      document.querySelector('.ov-compile-config [role="dialog"]');
    await act(async () =>
      edit()
        .querySelector('form')
        .dispatchEvent(
          new window.Event('submit', { bubbles: true, cancelable: true }),
        ),
    );
    await settle();
    assert.match(
      document.querySelector('.ov-toast--warning').textContent,
      /Enter at least one source directory/,
    );
    assert.equal(
      fetch.calls.some((call) => call.path.endsWith('/compile/config/update')),
      false,
    );
    assert.equal(Boolean(workflow().querySelector('[role="alert"]')), false);
    assert.equal(
      Boolean(document.querySelector('.ov-notification--error')),
      false,
    );
    assert.equal(edit().querySelector('input:checked').value, 'none');
    await click(button('Cancel', edit()));
  } finally {
    await view.unmount();
  }
});
