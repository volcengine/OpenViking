import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { act, createElement as h, createRef, Fragment } from 'react';
import { mount } from './helpers/module-dom.mjs';

const entry = (name) =>
  pathToFileURL(`${process.env.OV_UI_TEST_HOOKS}/${name}.js`).href;
const { Dropdown, DropdownItem, DropdownSeparator } = await import(
  entry('dropdown')
);
const { Tooltip } = await import(entry('tooltip'));
const { FilePreviewBreadcrumbMenu } = await import(entry('breadcrumb-menu'));
const menu = () => document.querySelector('[role="menu"]');
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

test('items preserve Tooltip wrappers, refs and custom content through keyboard selection', async () => {
  const selected = [];
  const itemRef = createRef();
  function WrappedItem() {
    return h(
      Tooltip,
      { content: 'Export the current document', delay: 0 },
      h(
        DropdownItem,
        { ref: itemRef, onClick: () => selected.push('export') },
        h('span', { 'data-icon': 'export', 'aria-hidden': true }),
        h('strong', null, 'Export'),
        h('span', { 'data-shortcut': true }, '⌘E'),
      ),
    );
  }
  const view = await mount(
    h(
      Dropdown,
      { trigger: h('button', null, 'More actions') },
      h(Fragment, null, h(DropdownItem, { disabled: true }, 'Unavailable')),
      h(DropdownSeparator),
      h('div', null, h(WrappedItem)),
      h(DropdownItem, null, 'Delete'),
    ),
  );
  try {
    const trigger = view.container.querySelector('button');
    await key(trigger, 'ArrowDown');
    assert.equal(document.activeElement, itemRef.current);
    assert.ok(itemRef.current.querySelector('strong'));
    assert.ok(itemRef.current.querySelector('[data-shortcut]'));
    assert.equal(menu().querySelectorAll('hr').length, 1);
    await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
    assert.equal(
      document.querySelector('[role="tooltip"]').textContent,
      'Export the current document',
    );
    await key(itemRef.current, 'ArrowDown');
    assert.equal(document.activeElement.textContent, 'Delete');
    await key(document.activeElement, 'ArrowDown');
    assert.equal(document.activeElement, itemRef.current);
    await click(itemRef.current);
    assert.deepEqual(selected, ['export']);
    assert.equal(menu(), null);
    assert.equal(document.querySelector('[role="tooltip"]'), null);
    assert.equal(document.activeElement, trigger);
  } finally {
    await view.unmount();
  }
});

test('conditional children participate in navigation without flattening their component tree', async () => {
  const content = (show) =>
    h(
      Dropdown,
      { trigger: h('button', null, 'Actions') },
      h(DropdownItem, null, 'First'),
      show && h('div', null, h(DropdownItem, null, 'Last')),
    );
  const view = await mount(content(false));
  try {
    const trigger = view.container.querySelector('button');
    await key(trigger, 'ArrowDown');
    await view.render(content(true));
    await key(document.activeElement, 'End');
    assert.equal(document.activeElement.textContent, 'Last');
    await key(document.activeElement, 'Escape');
    assert.equal(menu(), null);
    assert.equal(document.activeElement, trigger);
    assert.equal(trigger.getAttribute('aria-expanded'), 'false');
  } finally {
    await view.unmount();
  }
});

test('collapsed parent commands close after selection and retain disabled state', async () => {
  const opened = [];
  const props = {
    hiddenPathLabel: 'Hidden parents',
    segments: [
      { uri: 'viking://resources/team', label: 'Team' },
      { uri: 'viking://resources/team/docs', label: 'Docs' },
    ],
    canOpen: true,
    disabled: false,
    onOpen: (uri) => opened.push(uri),
  };
  const view = await mount(h(FilePreviewBreadcrumbMenu, props));
  try {
    const trigger = view.container.querySelector('button');
    await key(trigger, 'ArrowUp');
    assert.equal(document.activeElement.textContent, 'Docs/');
    await click(document.activeElement);
    assert.deepEqual(opened, ['viking://resources/team/docs']);
    assert.equal(menu(), null);
    assert.equal(document.activeElement, trigger);
    await view.render(
      h(FilePreviewBreadcrumbMenu, { ...props, disabled: true }),
    );
    await click(trigger);
    assert.ok(
      [...menu().querySelectorAll('button')].every((node) => node.disabled),
    );
    await key(menu(), 'Escape');
    assert.equal(menu(), null);
  } finally {
    await view.unmount();
  }
});
