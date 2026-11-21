import assert from 'node:assert/strict';
import test from 'node:test';
import {
  act,
  button,
  click,
  fixture,
  mount,
  ok,
  openAction,
  target,
  tree,
  workflow,
} from './helpers/preview-native-dom.mjs';
const config = {
  to: target,
  from: ['viking://resources/source'],
  skill: 'viking://user/alice/skills/compile',
  instruction: 'keep',
  auto_update: false,
  update_schedule: null,
};

test('schedule uses visible radio choices, the shared weekday picker and preserves drafts through mode changes', async () => {
  const fetch = fixture(
    target,
    true,
    (call) =>
      call.path.endsWith('/compile/config')
        ? ok(config)
        : call.path.endsWith('/compile/config/update')
          ? ok(call.body)
          : undefined,
    [{ to: target }],
  );
  const view = await mount(tree(fetch, target));
  try {
    await openAction('Compile configuration');
    await click(document.querySelector('[aria-label="Update schedule"]'));
    const edit = () =>
      document.querySelector('.ov-compile-config [role="dialog"]');
    assert.equal(edit().querySelector('select'), null);
    assert.deepEqual(
      [...edit().querySelectorAll('input[type="radio"]')].map(
        (input) => input.value,
      ),
      ['daily', 'weekly', 'none'],
    );
    const mode = (value) =>
      click(edit().querySelector(`input[value="${value}"]`));
    await mode('weekly');
    await click(button('Save', edit()));
    assert.match(
      edit().querySelector('[role="alert"]').textContent,
      /Select the day/,
    );
    await click(edit().querySelector('[role="combobox"]'));
    await click([...document.querySelectorAll('[role="option"]')].at(-1));
    const input = edit().querySelector('[name="schedule-time"]');
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(input, '10:45');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    await mode('daily');
    await mode('none');
    assert.equal(edit().querySelector('[name="schedule-time"]'), null);
    await mode('weekly');
    assert.equal(edit().querySelector('[name="schedule-time"]').value, '10:45');
    await click(button('Save', edit()));
    const saved = fetch.calls.find((call) =>
      call.path.endsWith('/compile/config/update'),
    );
    assert.deepEqual(saved.body, {
      ...config,
      auto_update: true,
      update_schedule: {
        frequency: 'weekly',
        hour: 10,
        minute: 45,
        weekday: 7,
      },
    });
    assert.match(workflow().textContent, /Configuration saved/);
  } finally {
    await view.unmount();
  }
});

for (const label of [
  'Update schedule',
  'Source directories',
  'Compilation Skill',
]) {
  test(`${label} closes when the host revokes write capability`, async () => {
    const fetch = fixture(
      target,
      true,
      (call) =>
        call.path.endsWith('/compile/config') ? ok(config) : undefined,
      [{ to: target }],
    );
    const view = await mount(tree(fetch, target));
    try {
      await openAction('Compile configuration');
      await click(document.querySelector(`[aria-label="${label}"]`));
      assert.equal(document.querySelectorAll('[role="dialog"]').length, 2);
      await view.render(tree(fetch, target, { readOnly: true }));
      assert.equal(document.querySelectorAll('[role="dialog"]').length, 1);
      assert.equal(
        document.querySelector(`[aria-label="${label}"]`).disabled,
        true,
      );
      assert.equal(fetch.countOf('/api/v1/compile/config/update'), 0);
      await view.render(tree(fetch, target));
      assert.equal(document.querySelectorAll('[role="dialog"]').length, 1);
    } finally {
      await view.unmount();
    }
  });
}
