import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import { mount } from './helpers/module-dom.mjs';

const click = (node) => act(async () => node.click());
const submit = (view) =>
  act(async () => {
    view.container
      .querySelector('form')
      .dispatchEvent(
        new window.Event('submit', { bubbles: true, cancelable: true }),
      );
  });
const input = (node, value) =>
  act(async () => {
    const prototype =
      node.tagName === 'TEXTAREA'
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(node, value);
    node.dispatchEvent(new window.Event('input', { bubbles: true }));
  });
const field = (node) => node.closest('.ov-form-field');
const error = (node) => field(node).querySelector('[role=alert]');
const assertInvalid = (node) => {
  assert.equal(node.getAttribute('aria-invalid'), 'true');
  assert.ok(
    node.getAttribute('aria-describedby').split(' ').includes(error(node).id),
  );
};
const assertCleared = (node) => {
  assert.equal(node.hasAttribute('aria-invalid'), false);
  assert.equal(error(node) === null, true);
  assert.equal(field(node).classList.contains('ov-form-field--error'), false);
};

test('text field keeps its label, hint and draft; editing clears submission errors', async () => {
  for (const multiline of [false, true]) {
    let attempts = 0;
    const view = await mount(
      h(VikingBotInteraction, {
        request: {
          kind: 'text',
          question: 'Name',
          description: 'Pick a name',
          initialValue: 'Draft',
          multiline,
        },
        onSubmit: () => {
          attempts++;
          throw new Error('Try another name');
        },
      }),
    );
    try {
      const control = view.container.querySelector('[name=answer]');
      assert.equal(
        field(control).querySelector('label').control === control,
        true,
      );
      assert.equal(
        document.getElementById(control.getAttribute('aria-describedby'))
          .textContent,
        'Pick a name',
      );
      await submit(view);
      assertInvalid(control);
      assert.equal(error(control).textContent, 'Try another name');
      await input(control, 'Edited');
      assertCleared(control);
      assert.equal(control.value, 'Edited');
      assert.equal(attempts, 1);
      assert.equal(
        document.getElementById(control.getAttribute('aria-describedby'))
          .textContent,
        'Pick a name',
      );
      await submit(view);
      assert.equal(attempts, 2);
      assertInvalid(control);
    } finally {
      await view.unmount();
    }
  }
});

test('choice and repair errors clear on selection without submitting', async () => {
  for (const kind of ['choice', 'repair']) {
    const view = await mount(
      h(VikingBotInteraction, {
        request: {
          kind,
          options: [{ value: 'a', id: 'a', label: 'A' }],
          allowCustomInput: kind === 'choice',
        },
        onSubmit: () => {
          throw new Error('Rejected');
        },
      }),
    );
    try {
      const group = view.container.querySelector('[role=radiogroup]');
      await submit(view);
      assertInvalid(group);
      await click(group.querySelector('input[value=a]'));
      assertCleared(group);
      await submit(view);
      assertInvalid(group);
      await click(group.querySelector('input[value=a]'));
      if (kind === 'choice') {
        await click(
          group.querySelector(
            '.ov-viking-bot__interaction-custom input[type=radio]',
          ),
        );
        await submit(view);
        assertInvalid(group);
        await input(group.querySelector('[name=custom-answer]'), '');
        await input(group.querySelector('[name=custom-answer]'), 'Custom');
        assertCleared(group);
      }
    } finally {
      await view.unmount();
    }
  }
});

test('schedule errors belong to the invalid field and clear on any value change', async () => {
  const request = {
    kind: 'schedule',
    timezone: 'Asia/Shanghai',
    initialValue: { mode: 'weekly', time: '25:70', weekday: 9 },
  };
  const render = (locale) => h(VikingBotInteraction, { request, locale });
  const view = await mount(render('en-US'));
  try {
    const time = view.container.querySelector('[name=schedule-time]');
    const weekday = view.container.querySelector('[role=combobox]');
    assert.equal(field(time).querySelector('label').control === time, true);
    assert.equal(
      field(weekday).querySelector('label').control === weekday,
      true,
    );
    await submit(view);
    assertInvalid(time);
    assertCleared(weekday);
    assert.equal(document.activeElement === time, true);
    await view.render(render('zh-CN'));
    assert.match(error(time).textContent, /HH:mm/);
    assert.equal(time.value, '25:70');
    await input(time, 'still invalid');
    assertCleared(time);
    assert.match(
      document.getElementById(time.getAttribute('aria-describedby'))
        .textContent,
      /Asia\/Shanghai/,
    );
    await submit(view);
    assertInvalid(time);
    await input(time, '12:30');
    await submit(view);
    assertInvalid(weekday);
    assertCleared(time);
    assert.equal(document.activeElement === weekday, true);
    await click(view.container.querySelector('input[value=none]'));
    assert.equal(view.container.querySelector('[role=alert]') === null, true);
  } finally {
    await view.unmount();
  }
});
