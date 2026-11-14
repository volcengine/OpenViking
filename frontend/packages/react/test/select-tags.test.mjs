import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { act, createElement as h, Fragment } from 'react';
import { mount } from './helpers/module-dom.mjs';

const { Select, SelectOption } = await import(
  pathToFileURL(`${process.env.OV_UI_TEST_HOOKS}/select.js`).href
);
const choices = (items) =>
  h(
    Fragment,
    null,
    ...items.map(([value, label]) =>
      h(SelectOption, { key: value, value }, label),
    ),
  );
test('tag folding responds to container width without changing values or hidden form fields', async () => {
  const OriginalObserver = globalThis.ResizeObserver;
  let measure;
  globalThis.ResizeObserver = class {
    constructor(callback) {
      this.callback = callback;
    }
    observe(node) {
      if (node.classList.contains('ov-select__tags')) measure = this.callback;
    }
    disconnect() {}
  };
  const view = await mount(
    h(
      Select,
      {
        multiple: true,
        name: 'items',
        defaultValue: ['long', 'audit', 'read'],
        maxTagCount: 3,
      },
      choices([
        ['long', 'A very long permission label'],
        ['audit', 'Audit'],
        ['read', 'Read'],
      ]),
    ),
  );
  try {
    const tags = view.container.querySelector('.ov-select__tags');
    let width = 220;
    Object.defineProperty(tags, 'clientWidth', { get: () => width });
    await act(async () => measure());
    assert.equal(tags.querySelectorAll('button').length, 3);
    width = 120;
    await act(async () => measure());
    assert.equal(tags.querySelectorAll('button').length, 1);
    assert.equal(tags.querySelector('.ov-select__tag-count').textContent, '+2');
    width = 40;
    await act(async () => measure());
    assert.equal(tags.querySelectorAll('button').length, 0);
    assert.equal(tags.querySelector('.ov-select__tag-count').textContent, '+3');
    assert.equal(
      view.container.querySelectorAll('input[name="items"]').length,
      3,
    );
    width = 300;
    await act(async () => measure());
    assert.equal(tags.querySelectorAll('button').length, 3);
  } finally {
    await view.unmount();
    globalThis.ResizeObserver = OriginalObserver;
  }
});
