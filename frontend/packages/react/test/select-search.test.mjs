import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { act, createElement as h, Fragment } from 'react';
import { mount } from './helpers/module-dom.mjs';

const entry = (name) =>
  pathToFileURL(`${process.env.OV_UI_TEST_HOOKS}/${name}.js`).href;
const { Select, SelectOption } = await import(entry('select'));
const { Tooltip } = await import(entry('tooltip'));
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
const search = () => document.querySelector('.ov-select__search input');
const field = (view) =>
  view.container.querySelector('[aria-haspopup="listbox"]');
const options = () => [...document.querySelectorAll('[role="option"]')];
const type = (value) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    ).set.call(search(), value);
    search().dispatchEvent(new window.Event('input', { bubbles: true }));
  });
const choices = () =>
  h(
    Fragment,
    null,
    h(
      Tooltip,
      { content: 'Read permission', delay: 0 },
      h(
        SelectOption,
        { value: 'read', label: 'Read' },
        h('strong', null, 'Read details'),
      ),
    ),
    h('span', null, h(SelectOption, { value: 'write' }, 'Write')),
    h(SelectOption, { value: 'manage' }, 'Manage'),
    h(SelectOption, { value: 'disabled', disabled: true }, 'Unavailable'),
  );

test('search filters decorated children without losing selections and resets on reopen', async () => {
  const changes = [];
  const view = await mount(
    h(
      Select,
      {
        searchable: true,
        multiple: true,
        defaultValue: ['write'],
        onChange: (next) => changes.push(next),
      },
      choices(),
    ),
  );
  try {
    await click(field(view));
    assert.equal(document.activeElement, search());
    await type(' ReAd ');
    assert.deepEqual(
      options().map((n) => n.textContent),
      ['Read details'],
    );
    assert.ok(options()[0].querySelector('strong'));
    await key(search(), 'ArrowDown');
    assert.equal(document.activeElement, options()[0]);
    await key(document.activeElement, 'Enter');
    assert.deepEqual(changes, [['write', 'read']]);
    assert.ok(search());
    await key(document.activeElement, 'Tab', { shiftKey: true });
    assert.equal(document.activeElement, search());
    await type('missing');
    assert.equal(options().length, 0);
    assert.equal(
      document.querySelector('[role="status"]').textContent,
      'No matching options',
    );
    await key(search(), 'Enter', { isComposing: true });
    assert.equal(changes.length, 1);
    await key(search(), 'Escape');
    assert.equal(search(), null);
    assert.equal(document.activeElement, field(view));
    await click(field(view));
    assert.equal(search().value, '');
    assert.equal(options().length, 4);
    assert.equal(options()[3].disabled, true);
  } finally {
    await view.unmount();
  }
});

test('tag folding limits presentation only and default or custom placeholders handle []', async () => {
  const view = await mount(
    h(
      Select,
      {
        multiple: true,
        name: 'permissions',
        defaultValue: ['read', 'write', 'manage'],
        locale: 'zh-CN',
      },
      choices(),
    ),
  );
  try {
    assert.equal(
      view.container.querySelectorAll('.ov-select__tag button').length,
      2,
    );
    assert.equal(
      view.container.querySelector('.ov-select__tag-count').textContent,
      '+1',
    );
    assert.equal(
      view.container.querySelectorAll('input[name="permissions"]').length,
      3,
    );
    await click(view.container.querySelector('[aria-label="移除Read"]'));
    assert.equal(view.container.querySelector('.ov-select__tag-count'), null);
    await click(view.container.querySelector('[aria-label="移除Write"]'));
    await click(view.container.querySelector('[aria-label="移除Manage"]'));
    assert.equal(field(view).textContent, '请选择');
    await view.render(
      h(
        Select,
        { multiple: true, placeholder: 'Choose permissions' },
        choices(),
      ),
    );
    assert.equal(field(view).textContent, 'Choose permissions');
    await view.render(
      h(
        Select,
        { multiple: true, value: ['read', 'write'], maxTagCount: 0 },
        choices(),
      ),
    );
    assert.equal(
      view.container.querySelectorAll('.ov-select__tag button').length,
      0,
    );
    assert.equal(
      view.container.querySelector('.ov-select__tag-count').textContent,
      '+2',
    );
  } finally {
    await view.unmount();
  }
});

test('whole-control disable closes the popup and prevents removing tags; errors expose aria-invalid', async () => {
  const render = (props) =>
    h(
      Select,
      {
        multiple: true,
        defaultValue: ['read'],
        ...props,
      },
      choices(),
    );
  const view = await mount(render({ status: 'error' }));
  try {
    assert.equal(field(view).getAttribute('aria-invalid'), 'true');
    await click(field(view));
    assert.ok(document.querySelector('[role="listbox"]'));
    await view.render(render({ disabled: true }));
    assert.equal(document.querySelector('[role="listbox"]'), null);
    assert.equal(field(view).disabled, true);
    assert.equal(
      view.container.querySelector('.ov-select__tag button').disabled,
      true,
    );
    await click(field(view));
    assert.equal(document.querySelector('[role="listbox"]'), null);
    await view.render(render({ 'aria-invalid': true }));
    assert.equal(field(view).getAttribute('aria-invalid'), 'true');
    assert.equal(document.querySelector('[role="listbox"]'), null);
    await view.render(render({}));
    assert.equal(field(view).hasAttribute('aria-invalid'), false);
  } finally {
    await view.unmount();
  }
});
