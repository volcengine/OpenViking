import { toastContent } from './helpers/feedback-dom.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import { mount } from './helpers/module-dom.mjs';

const click = (node) => act(async () => node.click());
const key = (node, value) =>
  act(async () =>
    node.dispatchEvent(
      new window.KeyboardEvent('keydown', {
        key: value,
        bubbles: true,
        cancelable: true,
      }),
    ),
  );
const submit = (view) =>
  act(async () =>
    view.container
      .querySelector('form')
      .dispatchEvent(
        new window.Event('submit', { bubbles: true, cancelable: true }),
      ),
  );
const input = async (node, value) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    ).set.call(node, value);
    node.dispatchEvent(new window.Event('input', { bubbles: true }));
  });
const request = {
  kind: 'schedule',
  initialValue: { mode: 'daily', time: '10:30' },
};
const radio = (view, value) =>
  view.container.querySelector(`input[type=radio][value=${value}]`);
const weekday = (view) => view.container.querySelector('[role=combobox]');
const time = (view) => view.container.querySelector('[name=schedule-time]');
const select = async (view, label) => {
  await key(weekday(view), 'ArrowDown');
  await click(
    [...document.querySelectorAll('.ov-select__popover [role=option]')].find(
      (node) => node.textContent === label,
    ),
  );
};

test('frequency changes preserve the weekday and time; disabled fields close their overlays', async () => {
  const answers = [];
  const render = (props = {}) =>
    h(VikingBotInteraction, {
      request,
      onSubmit: (value) => answers.push(value),
      ...props,
    });
  const view = await mount(render());
  try {
    await click(radio(view, 'weekly'));
    await select(view, 'Friday');
    assert.equal(time(view).value, '10:30');
    await click(radio(view, 'none'));
    assert.equal(time(view), null);
    await click(radio(view, 'daily'));
    await click(radio(view, 'weekly'));
    assert.equal(time(view).value, '10:30');
    assert.equal(weekday(view).textContent, 'Friday');
    await key(weekday(view), 'ArrowDown');
    assert.ok(document.querySelector('.ov-select__popover'));
    await view.render(render({ disabled: true }));
    assert.equal(document.querySelector('.ov-select__popover'), null);
    assert.ok(
      [...view.container.querySelectorAll('input,button')].every(
        (node) => node.disabled,
      ),
    );
    await click(radio(view, 'none'));
    assert.equal(weekday(view).textContent, 'Friday');
    await view.render(render());
    await click(time(view));
    assert.ok(document.querySelector('.ov-time-picker__popup'));
    await view.render(render({ submitting: true }));
    assert.equal(document.querySelector('.ov-time-picker__popup'), null);
    await view.render(render());
    assert.equal(document.querySelector('.ov-time-picker__popup'), null);
    await submit(view);
    assert.deepEqual(answers.at(-1), {
      kind: 'schedule',
      value: { mode: 'weekly', time: '10:30', weekday: 5 },
    });
  } finally {
    await view.unmount();
  }
});

test('time validation, keyboard panel selection, no-schedule answers and cancellation use the public contract', async () => {
  const answers = [];
  const view = await mount(
    h(VikingBotInteraction, {
      request,
      locale: 'zh-CN',
      onSubmit: (value) => answers.push(value),
      onCancel() {},
    }),
  );
  try {
    await input(time(view), '25:70');
    await submit(view);
    assert.equal(answers.length, 0);
    assert.match(toastContent().textContent, /HH:mm/);
    assert.equal(document.activeElement, time(view));
    await input(time(view), '16:45');
    assert.equal(Boolean(toastContent()), false);
    await key(time(view), 'ArrowDown');
    assert.equal(document.activeElement.textContent, '16');
    await key(document.activeElement, 'ArrowDown');
    await click(document.activeElement);
    assert.equal(time(view).value, '17:45');
    await key(document.activeElement, 'Escape');
    assert.equal(document.querySelector('.ov-time-picker__popup'), null);
    await submit(view);
    assert.deepEqual(answers.at(-1), {
      kind: 'schedule',
      value: { mode: 'daily', time: '17:45' },
    });
    await click(
      [...view.container.querySelectorAll('button')].find(
        (node) => node.textContent === '取消',
      ),
    );
    assert.equal(time(view).value, '10:30');
    await click(radio(view, 'none'));
    await submit(view);
    assert.deepEqual(answers.at(-1), {
      kind: 'schedule',
      value: { mode: 'none' },
    });
  } finally {
    await view.unmount();
  }
});
