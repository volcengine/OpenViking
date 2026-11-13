import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { act, createElement as h, Fragment } from 'react';
import { mount } from './helpers/module-dom.mjs';

const { Select, SelectOption } = await import(
  pathToFileURL(`${process.env.OV_UI_TEST_HOOKS}/select.js`).href
);
const click = (node) => act(async () => node.click());
const field = (view) =>
  view.container.querySelector('[aria-haspopup="listbox"]');
const options = () => [...document.querySelectorAll('[role="option"]')];
const choices = (items) =>
  h(
    Fragment,
    null,
    ...items.map(([value, label]) =>
      h(SelectOption, { key: value, value }, label),
    ),
  );
const type = (value) =>
  act(async () => {
    const input = document.querySelector('input[type="search"]');
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    ).set.call(input, value);
    input.dispatchEvent(new window.Event('input', { bubbles: true }));
  });

test('remote search reports the raw keyword and bypasses local label matching', async () => {
  const searches = [];
  const render = (props = {}, rows = [['read', '可阅读']]) =>
    h(
      Select,
      {
        searchable: true,
        filterOption: false,
        onSearch: (q) => searches.push(q),
        ...props,
      },
      choices(rows),
    );
  const view = await mount(render());
  try {
    await click(field(view));
    assert.deepEqual(searches, []);
    await type(' Reader ');
    assert.deepEqual(searches, [' Reader ']);
    assert.equal(options()[0].textContent, '可阅读');
    await view.render(render({ loading: true }));
    assert.equal(
      document.querySelector('[role="listbox"]').getAttribute('aria-busy'),
      'true',
    );
    assert.equal(options().length, 0);
    assert.equal(
      document.querySelector('[role="progressbar"]').textContent,
      'Loading options…',
    );
    assert.equal(document.querySelector('[role="status"]').textContent, '');
    assert.equal(field(view).hasAttribute('filteroption'), false);
    await type('editor');
    assert.equal(searches.at(-1), 'editor');
    await view.render(render({}, [['write', '可编辑']]));
    assert.equal(options()[0].textContent, '可编辑');
    assert.equal(document.querySelector('[role="progressbar"]'), null);
    await type('');
    assert.equal(searches.at(-1), '');
    await view.render(render({ filterOption: true }));
    await type('reader');
    assert.equal(options().length, 0);
  } finally {
    await view.unmount();
  }
});

test('selected labels survive result replacement, refresh when redeclared, and clear with selection', async () => {
  const changes = [];
  let selected;
  const render = (rows, value = undefined) =>
    h(
      Select,
      {
        multiple: true,
        searchable: true,
        filterOption: false,
        defaultValue: [0],
        value,
        name: 'permission',
        onChange: (next) => changes.push(next),
        renderValue: (items) => {
          selected = items;
          return items.map((n) => n.label).join(', ');
        },
      },
      choices(rows),
    );
  const view = await mount(render([[0, 'Zero']]));
  try {
    await click(field(view));
    await view.render(render([['write', 'Write']]));
    assert.equal(field(view).textContent, 'Zero');
    await click(options()[0]);
    assert.deepEqual(changes, [[0, 'write']]);
    assert.deepEqual(
      selected.map((n) => n.label),
      ['Zero', 'Write'],
    );
    await view.render(render([]));
    assert.equal(field(view).textContent, 'Zero, Write');
    assert.deepEqual(
      [...view.container.querySelectorAll('input[type="hidden"]')].map(
        (n) => n.value,
      ),
      ['0', 'write'],
    );
    await view.render(render([[0, 'Updated zero']]));
    assert.equal(field(view).textContent, 'Updated zero, Write');
    await view.render(render([]));
    assert.equal(field(view).textContent, 'Updated zero, Write');
    await view.render(render([], []));
    await view.render(render([], [0]));
    assert.equal(field(view).textContent, '0');
  } finally {
    await view.unmount();
  }
});

test('localized loading, caller error content and retry footer preserve search and focus', async () => {
  let retries = 0;
  const render = (props) =>
    h(Select, { searchable: true, locale: 'zh-CN', ...props }, null);
  const view = await mount(render({ loading: true }));
  try {
    await click(field(view));
    assert.equal(
      document.querySelector('[role="progressbar"]').getAttribute('aria-label'),
      '正在加载选项…',
    );
    await type('retained query');
    await view.render(
      render({
        emptyContent: '搜索失败，请重试。',
        popupFooter: h('button', { onClick: () => retries++ }, '重试'),
      }),
    );
    assert.equal(
      document.querySelector('[role="status"]').textContent,
      '搜索失败，请重试。',
    );
    assert.equal(
      document.querySelector('input[type="search"]').value,
      'retained query',
    );
    await click(
      [...document.querySelectorAll('button')].find(
        (n) => n.textContent === '重试',
      ),
    );
    assert.equal(retries, 1);
    await view.render(render({ emptyContent: null }));
    assert.equal(document.querySelector('[role="status"]').textContent, '');
  } finally {
    await view.unmount();
  }
});
