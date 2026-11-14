import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { act, createElement as h } from 'react';
import { mount } from './helpers/module-dom.mjs';

const { Select, SelectOption } = await import(
  pathToFileURL(`${process.env.OV_UI_TEST_HOOKS}/select.js`).href
);
const progress = () => document.querySelector('[role="progressbar"]');
const list = () => document.querySelector('[role="listbox"]');
const render = (props = {}) =>
  h(
    Select,
    {
      open: true,
      searchable: true,
      multiple: true,
      value: ['read'],
      ...props,
    },
    h(SelectOption, { value: 'read' }, 'Read'),
  );

test('data loading and query searching have separate controlled content and busy lifetimes', async () => {
  const content = {
    loadingContent: h('strong', null, 'Loading data'),
    searchingContent: h('em', null, 'Retrieving matches'),
  };
  const view = await mount(render({ ...content, loading: true }));
  try {
    assert.equal(progress().textContent, 'Loading data');
    assert.equal(progress().getAttribute('aria-label'), 'Loading options…');
    assert.equal(list().getAttribute('aria-busy'), 'true');
    await view.render(render({ ...content, searching: true }));
    assert.equal(
      progress().querySelector('em').textContent,
      'Retrieving matches',
    );
    assert.equal(progress().getAttribute('aria-label'), 'Searching options…');
    assert.equal(document.querySelector('[role="option"]'), null);
    await view.render(render({ ...content, loading: true, searching: true }));
    assert.equal(progress().textContent, 'Retrieving matches');
    await view.render(render({ ...content, loading: true }));
    assert.equal(progress().textContent, 'Loading data');
    await view.render(render(content));
    assert.equal(progress(), null);
    assert.equal(list().hasAttribute('aria-busy'), false);
    assert.equal(
      document.querySelector('[role="option"]').getAttribute('aria-selected'),
      'true',
    );
    assert.equal(view.container.querySelector('[searching]'), null);
  } finally {
    await view.unmount();
  }
});

test('search defaults, explicit empty searching content, and a keyword do not alias loading', async () => {
  const searches = [];
  const view = await mount(
    render({ searching: true, loadingContent: 'Only data loading' }),
  );
  try {
    assert.equal(progress().textContent, 'Searching options…');
    await view.render(render({ searching: true, locale: 'zh-CN' }));
    assert.equal(progress().textContent, '正在检索选项…');
    await view.render(render({ searching: true, searchingContent: null }));
    assert.equal(progress().textContent, '');
    assert.equal(list().getAttribute('aria-busy'), 'true');
    await view.render(
      render({ filterOption: false, onSearch: (q) => searches.push(q) }),
    );
    await act(async () => {
      const input = document.querySelector('input[type="search"]');
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(input, 'reader');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    assert.deepEqual(searches, ['reader']);
    assert.equal(progress(), null);
    assert.equal(list().hasAttribute('aria-busy'), false);
    assert.equal(document.querySelector('[role="option"]').textContent, 'Read');
  } finally {
    await view.unmount();
  }
});
