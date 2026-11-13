import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { act, createElement as h, Fragment } from 'react';
import { mount } from './helpers/module-dom.mjs';

const { Select, SelectOption } = await import(
  pathToFileURL(`${process.env.OV_UI_TEST_HOOKS}/select.js`).href
);
const list = () => document.querySelector('[role="listbox"]');
const options = () => [...list().querySelectorAll('[role="option"]')];
const choices = () =>
  h(
    Fragment,
    null,
    h(SelectOption, { value: 'a' }, 'Alpha'),
    h(SelectOption, { value: 'b' }, 'Beta'),
  );
async function key(node, value, extra = {}) {
  const event = new window.KeyboardEvent('keydown', {
    key: value,
    bubbles: true,
    cancelable: true,
    ...extra,
  });
  await act(async () => node.dispatchEvent(event));
  return event;
}
async function press(node) {
  await act(async () => {
    node.focus();
    node.dispatchEvent(
      new window.PointerEvent('pointerdown', { bubbles: true }),
    );
    node.click();
  });
}

test('pointer opening and reopening focus the selected option without changing its value', async () => {
  const changes = [];
  const view = await mount(
    h(
      Select,
      { defaultValue: ['b'], onChange: (v) => changes.push(v) },
      choices(),
    ),
  );
  try {
    const trigger = view.container.querySelector('[aria-haspopup="listbox"]');
    await press(trigger);
    assert.ok(document.activeElement === options()[1]);
    await key(document.activeElement, 'Escape');
    assert.equal(list(), null);
    assert.equal(document.activeElement, trigger);
    await press(trigger);
    assert.ok(document.activeElement === options()[1]);
    assert.deepEqual(changes, []);
  } finally {
    await view.unmount();
  }
});

test('empty pointer opening starts at the first option while ArrowUp preserves its last-option boundary', async () => {
  const view = await mount(h(Select, null, choices()));
  try {
    const trigger = view.container.querySelector('[aria-haspopup="listbox"]');
    await press(trigger);
    assert.ok(document.activeElement === options()[0]);
    await key(document.activeElement, 'Escape');
    await key(trigger, 'ArrowUp');
    assert.ok(document.activeElement === options()[1]);
  } finally {
    await view.unmount();
  }
});

test('footer actions keep native Tab order until the first and last actionable boundaries', async () => {
  const view = await mount(
    h(
      Select,
      {
        popupFooter: h(
          Fragment,
          null,
          h('button', { disabled: true }, 'Unavailable'),
          h('button', null, 'Retry'),
          h('a', { href: '#help' }, 'Help'),
          h('button', { tabIndex: -1 }, 'Excluded'),
        ),
      },
      choices(),
    ),
  );
  try {
    const trigger = view.container.querySelector('[aria-haspopup="listbox"]');
    await key(trigger, 'ArrowDown');
    await key(document.activeElement, 'Tab');
    const retry = document.activeElement;
    assert.equal(retry.textContent, 'Retry');
    const help = document.querySelector('a[href="#help"]');
    assert.equal((await key(retry, 'Tab')).defaultPrevented, false);
    assert.ok(list(), 'Tab within the footer must keep the popup open');
    // jsdom does not implement the browser's default Tab focus movement.
    await act(async () => help.focus());
    assert.equal(
      (await key(help, 'Tab', { shiftKey: true })).defaultPrevented,
      false,
    );
    assert.equal(document.activeElement, help);
    await act(async () => retry.focus());
    assert.equal(
      (await key(retry, 'Tab', { shiftKey: true })).defaultPrevented,
      true,
    );
    assert.equal(document.activeElement.getAttribute('role'), 'option');
    await key(document.activeElement, 'Tab');
    await act(async () => help.focus());
    await key(help, 'Tab');
    assert.equal(list(), null);
    assert.equal(document.activeElement, trigger);
  } finally {
    await view.unmount();
  }
});
