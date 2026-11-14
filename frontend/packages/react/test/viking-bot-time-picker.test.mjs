import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, useState } from 'react';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import { Modal } from './helpers/internal-hooks.mjs';
import { mount } from './helpers/module-dom.mjs';

const { TimePicker } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'time-picker.js'))
);

function TimeInput({ initialValue = '23:59' }) {
  const [value, onChange] = useState(initialValue);
  return h(TimePicker, {
    value,
    onChange,
    name: 'schedule-time',
    'aria-label': 'Update time',
    hourLabel: 'Hours',
    minuteLabel: 'Minutes',
  });
}

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
const request = {
  kind: 'schedule',
  initialValue: { mode: 'daily', time: '23:59' },
};
const popup = () => document.querySelector('.ov-time-picker__popup');

test('time columns retain the popup on repeated input clicks and select midnight with the keyboard', async () => {
  const answers = [];
  const view = await mount(
    h(VikingBotInteraction, {
      request,
      onSubmit: (value) => answers.push(value),
    }),
  );
  try {
    const input = view.container.querySelector('[name=schedule-time]');
    await act(async () => input.focus());
    await click(input);
    const panel = popup();
    const columns = panel.querySelectorAll('[role=listbox]');
    assert.equal(columns[0].querySelectorAll('[role=option]').length, 24);
    assert.equal(columns[1].querySelectorAll('[role=option]').length, 60);
    assert.equal(
      columns[0].querySelector('[aria-selected=true]').textContent,
      '23',
    );
    assert.equal(
      columns[1].querySelector('[aria-selected=true]').textContent,
      '59',
    );
    await click(input);
    assert.equal(popup(), panel);
    await key(input, 'ArrowDown');
    await key(document.activeElement, 'Home');
    await key(document.activeElement, 'Enter');
    assert.equal(input.value, '00:59');
    await act(async () =>
      columns[1].querySelector('[aria-selected=true]').focus(),
    );
    await key(document.activeElement, 'Home');
    await key(document.activeElement, ' ');
    assert.equal(input.value, '00:00');
    assert.equal(
      answers.length,
      0,
      'Enter on a time cell must not submit the form',
    );
    await key(document.activeElement, 'Escape');
    assert.equal(popup(), null);
    assert.equal(document.activeElement, input);
    await act(async () =>
      view.container
        .querySelector('form')
        .dispatchEvent(
          new window.Event('submit', { bubbles: true, cancelable: true }),
        ),
    );
    assert.deepEqual(answers[0], {
      kind: 'schedule',
      value: { mode: 'daily', time: '00:00' },
    });
  } finally {
    await view.unmount();
  }
});

test('time selection inside a focus-locked Modal keeps the input focused and closes on blur or Escape', async () => {
  let modalCloses = 0;
  const view = await mount(
    h(
      Modal,
      {
        open: true,
        title: 'Schedule',
        triggerRef: { current: null },
        onClose: () => modalCloses++,
      },
      h(TimeInput),
    ),
  );
  try {
    const input = document.querySelector('[name=schedule-time]');
    await act(async () => input.focus());
    await click(input);
    const hour = [
      ...popup()
        .querySelectorAll('[role=listbox]')[0]
        .querySelectorAll('[role=option]'),
    ].find((node) => node.textContent === '10');
    const down = new window.MouseEvent('mousedown', {
      button: 0,
      bubbles: true,
      cancelable: true,
    });
    await act(async () => hour.dispatchEvent(down));
    assert.equal(down.defaultPrevented, true);
    await click(hour);
    assert.equal(input.value, '10:59');
    assert.equal(document.activeElement, input);
    await key(input, 'Escape');
    assert.equal(popup(), null);
    assert.equal(modalCloses, 0, 'Escape on the input only closes its popup');
    await click(input);
    await key(input, 'ArrowDown');
    await key(document.activeElement, 'Escape');
    assert.equal(popup(), null);
    assert.equal(modalCloses, 0);
    assert.equal(document.activeElement, input);
    await click(input);
    await act(async () => document.querySelector('.ov-modal__close').focus());
    assert.equal(popup(), null);
    await act(async () => input.focus());
    await key(input, 'Escape');
    assert.equal(modalCloses, 1, 'Escape without a popup reaches the Modal');
  } finally {
    await view.unmount();
  }
});

test('overlapping wheel selections preserve updates to both time columns', async () => {
  const view = await mount(h(TimeInput, { initialValue: '09:00' }));
  try {
    const input = view.container.querySelector('[name=schedule-time]');
    await click(input);
    const columns = [...popup().querySelectorAll('.ov-time-picker__list')];
    for (const column of columns) {
      Object.defineProperty(column, 'clientHeight', { value: 224 });
      Object.defineProperty(column.firstElementChild, 'clientHeight', {
        value: 224 + 28 * (column.querySelectorAll('[role=option]').length - 1),
      });
    }
    const wheel = (column, value) => {
      column.scrollTop = value * 28;
      column.dispatchEvent(new window.WheelEvent('wheel', { bubbles: true }));
    };
    const flushWheel = () =>
      act(() => new Promise((resolve) => setTimeout(resolve, 140)));
    await act(async () => wheel(columns[1], 1));
    await click(columns[0].querySelectorAll('[role=option]')[10]);
    assert.equal(input.value, '10:00');
    await flushWheel();
    assert.equal(input.value, '10:01');
    await act(async () => {
      wheel(columns[0], 11);
      wheel(columns[1], 2);
    });
    await flushWheel();
    assert.equal(input.value, '11:02');
  } finally {
    await view.unmount();
  }
});
