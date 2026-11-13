import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { act, createElement as h, Fragment } from 'react';
import { renderToString } from 'react-dom/server';
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
const list = () => document.querySelector('[role="listbox"]');
const field = (view) =>
  view.container.querySelector('[aria-haspopup="listbox"]');
const options = () => [...list().querySelectorAll('[role="option"]')];
const choices = () =>
  h(
    Fragment,
    null,
    h(SelectOption, { value: 0 }, 'Zero'),
    h(SelectOption, { value: 1, disabled: true }, 'Unavailable'),
    h(SelectOption, { value: 2 }, 'Two'),
  );

test('single selection uses arrays, replaces one value, skips disabled options and restores focus', async () => {
  const changes = [];
  const view = await mount(
    h(
      Select,
      { defaultValue: [0], onChange: (next) => changes.push(next) },
      choices(),
    ),
  );
  try {
    assert.equal(field(view).textContent, 'Zero');
    await key(field(view), 'ArrowDown');
    assert.equal(document.activeElement.textContent, 'Zero');
    await key(document.activeElement, 'ArrowDown');
    assert.equal(document.activeElement.textContent, 'Two');
    await key(document.activeElement, 'Enter');
    assert.deepEqual(changes, [[2]]);
    assert.equal(list(), null);
    assert.equal(document.activeElement, field(view));
    assert.equal(field(view).textContent, 'Two');
    await key(field(view), 'ArrowDown');
    assert.equal(document.activeElement.textContent, 'Two');
    await key(document.activeElement, 'Home');
    assert.equal(document.activeElement.textContent, 'Zero');
    await key(document.activeElement, 'Escape');
    assert.equal(
      field(view).textContent,
      'Two',
      'navigation does not change the selected value',
    );
  } finally {
    await view.unmount();
  }
});

test('multiple selection toggles values, keeps the list open, and removes the last tag as []', async () => {
  const changes = [];
  const view = await mount(
    h(
      Select,
      {
        multiple: true,
        name: 'permissions',
        onChange: (next) => changes.push(next),
      },
      choices(),
    ),
  );
  try {
    await click(field(view));
    await click(options()[0]);
    await click(options()[2]);
    assert.ok(list());
    assert.equal(list().getAttribute('aria-multiselectable'), 'true');
    assert.deepEqual(
      [...view.container.querySelectorAll('input[name="permissions"]')].map(
        (n) => n.value,
      ),
      ['0', '2'],
    );
    await click(view.container.querySelector('[aria-label="Remove Zero"]'));
    await click(view.container.querySelector('[aria-label="Remove Two"]'));
    assert.deepEqual(changes, [[0], [0, 2], [2], []]);
    assert.equal(view.container.querySelectorAll('.ov-select__tag').length, 0);
  } finally {
    await view.unmount();
  }
});

test('controlled values, empty renderValue and rich labels render before the popup mounts and on SSR', async () => {
  const valuesSeen = [];
  const child = h(
    Tooltip,
    { content: 'Explanation', delay: 0 },
    h(
      SelectOption,
      { value: 'rich', label: 'Short label' },
      h('strong', null, 'Rich row'),
      h('span', null, 'Description'),
    ),
  );
  const render = (value, custom = true) =>
    h(
      Select,
      {
        value,
        placeholder: 'Choose',
        renderValue: custom
          ? (selected) => {
              valuesSeen.push(selected.map((o) => o.value));
              return h(
                'em',
                null,
                selected.length ? selected[0].label : 'Nothing chosen',
              );
            }
          : undefined,
      },
      child,
    );
  assert.match(renderToString(render(['rich'], false)), /Short label/);
  const view = await mount(render([]));
  try {
    assert.equal(field(view).textContent, 'Nothing chosen');
    assert.deepEqual(valuesSeen.at(-1), []);
    await view.render(render(['rich']));
    assert.equal(field(view).textContent, 'Short label');
    await key(field(view), 'ArrowDown');
    assert.ok(list().querySelector('strong'));
    assert.equal(options()[0].getAttribute('aria-selected'), 'true');
    await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
    assert.equal(
      document.querySelector('[role="tooltip"]').textContent,
      'Explanation',
    );
    await click(options()[0]);
    await view.render(render([]));
    assert.equal(field(view).textContent, 'Nothing chosen');
  } finally {
    await view.unmount();
  }
});
