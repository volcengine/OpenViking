import { notificationError } from './helpers/feedback-dom.mjs';
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
  button,
  click,
} from './helpers/preview-native-dom.mjs';
const config = {
  to: target,
  from: ['viking://resources/source'],
  skill: 'viking://user/alice/skills/compile',
  instruction: 'initial',
  auto_update: true,
  update_schedule: { frequency: 'weekly', hour: 9, minute: 30, weekday: 2 },
};
test('compile configuration uses the nearest registered root and preserves draft/schedule across failed saves and locale changes', async () => {
  const uri = `${target}/nested`;
  let fail = true;
  const fetch = fixture(
    uri,
    true,
    (call) => {
      if (call.path.endsWith('/compile/config'))
        return ok({ ...config, to: uri });
      if (call.path.endsWith('/compile/config/update'))
        return fail
          ? Promise.resolve(new Response('retry', { status: 503 }))
          : ok(call.body);
    },
    [{ to: target }, { to: uri }],
  );
  const view = await mount(tree(fetch, uri));
  try {
    await openAction('Compile configuration');
    assert.deepEqual(
      fetch.calls.find((call) => call.path.endsWith('/compile/config')).query,
      { to: uri },
    );
    await click(document.querySelector('[aria-label="Update schedule"]'));
    const edit = () =>
      document.querySelector('.ov-compile-config [role="dialog"]');
    const input = edit().querySelector('input[name="schedule-time"]');
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(input, '10:45');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    const save = () => click(button('Save', edit()) ?? button('保存', edit()));
    await save();
    assert.ok(notificationError());
    const count = fetch.calls.length;
    await view.render(tree(fetch, uri, { locale: 'zh-CN' }));
    assert.equal(fetch.calls.length, count);
    assert.equal(
      edit().querySelector('input[name="schedule-time"]').value,
      '10:45',
    );
    fail = false;
    await save();
    const writes = fetch.calls.filter((call) =>
      call.path.endsWith('/compile/config/update'),
    );
    assert.deepEqual(writes[1].body, {
      ...config,
      to: uri,
      update_schedule: {
        frequency: 'weekly',
        hour: 10,
        minute: 45,
        weekday: 2,
      },
    });
    assert.equal(edit(), null);
    assert.match(
      workflow().querySelector('[role="status"]').textContent,
      /配置已保存/,
    );
  } finally {
    await view.unmount();
  }
});

test('readOnly allows viewing target settings, while hiding configure removes its default action', async () => {
  const fetch = fixture(
    target,
    true,
    (call) => (call.path.endsWith('/compile/config') ? ok(config) : undefined),
    [{ to: target }],
  );
  const view = await mount(tree(fetch, target, { readOnly: true }));
  try {
    await openAction('Compile configuration');
    assert.equal(
      document.querySelector('[aria-label="Update schedule"]').disabled,
      true,
    );
    assert.equal(button('Save', workflow()), undefined);
    assert.equal(
      fetch.calls.some((call) => call.path.endsWith('/compile/config/update')),
      false,
    );
    await click(
      document.querySelector(
        '.ov-file-browser-workflow button[aria-label="Close dialog"]',
      ),
    );
    await view.render(
      tree(fetch, target, { previewActions: { configure: false } }),
    );
    await openAction('Version history');
    assert.match(workflow().textContent, /Version history/);
  } finally {
    await view.unmount();
  }
});

test('nearer target metadata wins over a catalog ancestor for configuration', async () => {
  const uri = `${target}/nested`;
  const fetch = fixture(
    uri,
    true,
    (call) => {
      if (call.path.endsWith('/fs/attrs') && call.uri === uri)
        return ok({ attrs: { tags: ['compile_target=true'] } });
      if (call.path.endsWith('/compile/config'))
        return ok({ ...config, to: uri });
    },
    [{ to: target }],
  );
  const view = await mount(tree(fetch, uri));
  try {
    await openAction('Compile configuration');
    assert.equal(
      fetch.calls.find((call) => call.path.endsWith('/compile/config')).query
        .to,
      uri,
    );
  } finally {
    await view.unmount();
  }
});

test('all three read-only compile paths expose their complete text through keyboard tooltips', async () => {
  const fetch = fixture(
    target,
    true,
    (call) => (call.path.endsWith('/compile/config') ? ok(config) : undefined),
    [{ to: target }],
  );
  const view = await mount(tree(fetch, target, { readOnly: true }));
  try {
    await openAction('Compile configuration');
    const paths = [
      ...document.querySelectorAll('.ov-compile-config__value[tabindex="0"]'),
    ];
    assert.equal(paths.length, 3);
    for (const [index, path] of paths.entries()) {
      await act(async () => {
        path.focus();
        await new Promise((resolve) => setTimeout(resolve, 350));
      });
      assert.equal(
        document.querySelector('[role="tooltip"]').textContent,
        [config.from[0], target, config.skill][index],
      );
    }
    assert.equal(fetch.countOf('/api/v1/compile/config/update'), 0);
  } finally {
    await view.unmount();
  }
});
