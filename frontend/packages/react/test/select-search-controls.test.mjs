import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { act, createElement as h } from 'react';
import { mount } from './helpers/module-dom.mjs';

const { Select, SelectOption } = await import(
  pathToFileURL(`${process.env.OV_UI_TEST_HOOKS}/select.js`).href
);
const input = () => document.querySelector('input[type="search"]');
const clear = () => document.querySelector('.ov-select__clear');
const click = (node) => act(async () => node.click());
const key = (node, value, extra = {}) =>
  act(async () => {
    node.dispatchEvent(
      new window.KeyboardEvent('keydown', {
        key: value,
        bubbles: true,
        cancelable: true,
        ...extra,
      }),
    );
  });
const type = (value) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    ).set.call(input(), value);
    input().dispatchEvent(new window.Event('input', { bubbles: true }));
  });

test('search clear is keyboard reachable, reports empty query, and restores input focus', async () => {
  const searches = [];
  const view = await mount(
    h(
      Select,
      {
        searchable: true,
        multiple: true,
        defaultValue: ['read'],
        onSearch: (q) => searches.push(q),
        locale: 'zh-CN',
      },
      h(SelectOption, { value: 'read' }, '可阅读'),
    ),
  );
  try {
    const trigger = view.container.querySelector('[aria-haspopup="listbox"]');
    await click(trigger);
    assert.equal(clear(), null);
    await type('可');
    assert.equal(clear().getAttribute('aria-label'), '清空搜索');
    assert.equal(clear().closest('[aria-hidden="true"]'), null);
    await key(input(), 'Tab');
    assert.equal(document.activeElement, clear());
    await key(clear(), 'Tab', { shiftKey: true });
    assert.equal(document.activeElement, input());
    await key(input(), 'Tab');
    await key(clear(), 'Tab');
    assert.equal(document.activeElement.getAttribute('role'), 'option');
    await key(document.activeElement, 'Tab', { shiftKey: true });
    assert.equal(document.activeElement, input());
    await click(clear());
    assert.equal(input().value, '');
    assert.equal(clear(), null);
    assert.equal(document.activeElement, input());
    assert.deepEqual(searches, ['可', '']);
    assert.equal(trigger.textContent, '可阅读');
    await type('可');
    await key(input(), 'Tab');
    await key(clear(), 'Escape');
    assert.equal(input(), null);
    assert.equal(document.activeElement, trigger);
  } finally {
    await view.unmount();
  }
});

test('custom loading content replaces the default while preserving busy and progress semantics', async () => {
  const render = (props = {}) =>
    h(
      Select,
      {
        open: true,
        searchable: true,
        loading: true,
        ...props,
      },
      h(SelectOption, { value: 'read' }, 'Read'),
    );
  const view = await mount(
    render({ loadingContent: h('strong', null, 'Loading permissions') }),
  );
  try {
    const progress = () => document.querySelector('[role="progressbar"]');
    assert.equal(
      progress().querySelector('strong').textContent,
      'Loading permissions',
    );
    assert.equal(progress().getAttribute('aria-label'), 'Loading options…');
    assert.equal(
      document.querySelector('[role="listbox"]').getAttribute('aria-busy'),
      'true',
    );
    assert.equal(document.querySelector('[role="option"]'), null);
    assert.equal(view.container.querySelector('[loadingcontent]'), null);
    await view.render(render({ locale: 'zh-CN' }));
    assert.equal(progress().textContent, '正在加载选项…');
    await view.render(render({ loadingContent: null }));
    assert.equal(progress().textContent, '');
    await view.render(
      render({ loading: false, loadingContent: 'Hidden when idle' }),
    );
    assert.equal(progress(), null);
    assert.equal(document.querySelector('[role="option"]').textContent, 'Read');
  } finally {
    await view.unmount();
  }
});
