import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { act, createElement as h, Fragment, useState } from 'react';
import { mount } from './helpers/module-dom.mjs';

const entry = (name) =>
  pathToFileURL(`${process.env.OV_UI_TEST_HOOKS}/${name}.js`).href;
const { Select, SelectOption } = await import(entry('select'));
const click = (node) => act(async () => node.click());
const key = (node, value, extra = {}) =>
  act(async () =>
    node.dispatchEvent(
      new window.KeyboardEvent('keydown', {
        key: value,
        bubbles: true,
        cancelable: true,
        ...extra,
      }),
    ),
  );
const list = () => document.querySelector('[role="listbox"]');
const field = (view) =>
  view.container.querySelector('[aria-haspopup="listbox"]');
const choices = () =>
  h(
    Fragment,
    null,
    h(SelectOption, { value: 0 }, 'Zero'),
    h(SelectOption, { value: 1, disabled: true }, 'Unavailable'),
    h(SelectOption, { value: 2 }, 'Two'),
  );

test('custom triggers can own activation while preserving refs, keyboard behavior and controlled visibility', async () => {
  function Custom() {
    const [open, setOpen] = useState(false);
    return h(
      Select,
      {
        open,
        onOpenChange: setOpen,
        renderValue: (selected) =>
          selected.length ? selected[0].label : 'Choose here',
        renderTrigger: ({ triggerRef, triggerProps, content }) =>
          h(
            'button',
            {
              ...triggerProps,
              ref: triggerRef,
              className: 'custom-trigger',
              onClick: () => {},
              onDoubleClick: () => setOpen(true),
            },
            content,
          ),
      },
      choices(),
    );
  }
  const view = await mount(h(Custom));
  try {
    assert.equal(view.container.querySelector('.ov-input'), null);
    await click(field(view));
    assert.equal(list(), null);
    await act(async () =>
      field(view).dispatchEvent(
        new window.MouseEvent('dblclick', { bubbles: true }),
      ),
    );
    assert.ok(list());
    await key(field(view), 'ArrowDown');
    assert.equal(document.activeElement.textContent, 'Zero');
    await key(document.activeElement, 'Escape');
    assert.equal(list(), null);
    assert.equal(document.activeElement, field(view));
  } finally {
    await view.unmount();
  }
});

test('typeahead, composition, outside dismissal, disabled fields and independent instances', async () => {
  const view = await mount(
    h(
      'div',
      null,
      h(Select, { variant: 'simple', 'aria-label': 'First' }, choices()),
      h(Select, { disabled: true, defaultValue: [0] }, choices()),
      h('button', { id: 'outside' }, 'Outside'),
    ),
  );
  try {
    const triggers = view.container.querySelectorAll(
      '[aria-haspopup="listbox"]',
    );
    await key(triggers[1], 'ArrowDown');
    assert.equal(list(), null);
    await key(triggers[0], 'ArrowDown');
    await key(document.activeElement, 't', { isComposing: true });
    assert.equal(document.activeElement.textContent, 'Zero');
    await key(document.activeElement, 't');
    assert.equal(document.activeElement.textContent, 'Two');
    await act(async () => view.container.querySelector('#outside').focus());
    assert.equal(list(), null);
    assert.equal(triggers[0].getAttribute('aria-expanded'), 'false');
    assert.equal(triggers[1].textContent, 'Zero');
  } finally {
    await view.unmount();
  }
});

test('footer pagination remains keyboard reachable and Escape closes the selector', async () => {
  let pages = 0;
  const view = await mount(
    h(
      Select,
      {
        popupFooter: h('button', { onClick: () => pages++ }, 'Load more'),
      },
      choices(),
    ),
  );
  try {
    await key(field(view), 'ArrowDown');
    await key(document.activeElement, 'Tab');
    assert.equal(document.activeElement.textContent, 'Load more');
    await click(document.activeElement);
    assert.equal(pages, 1);
    assert.ok(list());
    await key(document.activeElement, 'Escape');
    assert.equal(list(), null);
    assert.equal(document.activeElement, field(view));
  } finally {
    await view.unmount();
  }
});
