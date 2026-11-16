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
  settle,
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
    const input = workflow().querySelectorAll('textarea')[1];
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype,
        'value',
      ).set.call(input, 'changed instruction');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    await submit();
    assert.ok(notificationError());
    assert.equal(Boolean(workflow().querySelector('[role="alert"]')), false);
    const count = fetch.calls.length;
    await view.render(tree(fetch, uri, { locale: 'zh-CN' }));
    assert.equal(fetch.calls.length, count);
    assert.equal(
      workflow().querySelectorAll('textarea')[1].value,
      'changed instruction',
    );
    assert.equal(button('保存', workflow()).disabled, false);
    fail = false;
    await submit();
    const writes = fetch.calls.filter((call) =>
      call.path.endsWith('/compile/config/update'),
    );
    assert.deepEqual(writes[1].body, {
      ...config,
      to: uri,
      instruction: 'changed instruction',
    });
    assert.match(
      workflow().querySelector('[role="status"]').textContent,
      /配置已保存/,
    );
    assert.equal(Boolean(notificationError()), false);
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
    assert.equal(workflow().querySelector('fieldset').disabled, true);
    assert.equal(button('Save', workflow()), undefined);
    await submit();
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
