import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import { mount } from './helpers/module-dom.mjs';

const input = (node, value) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    ).set.call(node, value);
    node.dispatchEvent(new window.Event('input', { bubbles: true }));
  });

test('text is required by default, optional text stays empty, and validation never trims submitted content', async () => {
  const answers = [];
  const render = (extra = {}) =>
    h(VikingBotInteraction, {
      request: { kind: 'text', multiline: false, ...extra },
      onSubmit: (answer) => answers.push(answer),
    });
  const view = await mount(render());
  const submit = () => view.container.querySelector('button[type=submit]');
  try {
    assert.equal(submit().disabled, true);
    assert.equal(view.container.querySelector('[role=alert]'), null);
    await input(view.container.querySelector('input'), ' \t ');
    assert.equal(submit().disabled, true);
    await input(view.container.querySelector('input'), '  Draft  ');
    assert.equal(submit().disabled, false);
    await act(async () => submit().click());
    assert.deepEqual(answers, [{ kind: 'text', value: '  Draft  ' }]);
    await input(view.container.querySelector('input'), '');
    assert.equal(submit().disabled, true);
    await view.render(render({ required: false }));
    assert.equal(submit().disabled, false);
    await act(async () => submit().click());
    assert.deepEqual(answers.at(-1), { kind: 'text', value: '' });
  } finally {
    await view.unmount();
  }
});

test('invalid schedules and absent targets disable submission; valid schedules can submit', async () => {
  const view = await mount(
    h(VikingBotInteraction, {
      request: {
        kind: 'schedule',
        initialValue: { mode: 'daily', time: '25:00' },
      },
    }),
  );
  const submit = () => view.container.querySelector('button[type=submit]');
  try {
    assert.equal(submit().disabled, true);
    await input(view.container.querySelector('[name=schedule-time]'), '10:30');
    assert.equal(submit().disabled, false);
    await view.render(
      h(VikingBotInteraction, {
        request: { kind: 'target', directoryPicker: { rows: [] } },
      }),
    );
    assert.equal(submit().disabled, true);
  } finally {
    await view.unmount();
  }
});
