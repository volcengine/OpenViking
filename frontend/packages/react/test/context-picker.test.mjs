import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, useState } from 'react';
import { ContextPicker } from '@openviking/react/components/context-picker';
import { mount } from './helpers/module-dom.mjs';

const folder = (name, extra = {}) => ({
  kind: 'entry',
  type: 'directory',
  name,
  uri: `viking://resources/${name}`,
  id: name,
  depth: 0,
  hasChildren: false,
  ...extra,
});
const click = (node) =>
  act(async () => {
    node.focus();
    node.click();
  });
const dialog = () => document.querySelector('.ov-context-picker [role=dialog]');
const button = (name) =>
  [...dialog().querySelectorAll('button')].find(
    (node) => node.textContent === name,
  );
const item = (id) => dialog().querySelector(`[data-file-tree-row-id="${id}"]`);

// Selection belongs to the owner, including entries absent from filtered rows.
test('multiple selection is independent and survives filtering and replacement row ids', async () => {
  let confirmed;
  const parent = folder('wiki');
  const child = folder('wiki/notes', { depth: 1 });
  function Host({ rows }) {
    const [value, setValue] = useState([]);
    return h(ContextPicker, {
      open: true,
      multiple: true,
      autoReveal: false,
      rows,
      value,
      onChange: setValue,
      onConfirm: (next) => {
        confirmed = next;
      },
      onCancel() {},
    });
  }
  const view = await mount(h(Host, { rows: [parent, child] }));
  try {
    assert.equal(
      dialog()
        .querySelector('[role=tree]')
        .getAttribute('aria-multiselectable'),
      'true',
    );
    await click(item('wiki'));
    await click(item('wiki/notes').querySelector('input'));
    assert.equal(item('wiki').getAttribute('aria-selected'), 'true');
    assert.equal(item('wiki/notes').getAttribute('aria-selected'), 'true');
    await view.render(h(Host, { rows: [] }));
    assert.match(dialog().textContent, /2 selected/);
    await click(button('Confirm'));
    assert.deepEqual(
      confirmed.map((entry) => entry.uri),
      [parent.uri, child.uri],
    );
    await view.render(h(Host, { rows: [{ ...parent, id: 'new-parent-id' }] }));
    assert.equal(item('new-parent-id').getAttribute('aria-selected'), 'true');
    await click(item('new-parent-id'));
    assert.match(dialog().textContent, /1 selected/);
  } finally {
    await view.unmount();
  }
});

test('late rows restore URI selection; the host owns confirmation, errors and open state', async () => {
  let calls = 0;
  const selected = folder('wiki');
  const render = (extra = {}) =>
    h(ContextPicker, {
      open: true,
      autoReveal: false,
      rows: [],
      value: [selected],
      isReady: false,
      onConfirm: () => {
        calls++;
      },
      onCancel() {},
      ...extra,
    });
  const view = await mount(render());
  try {
    assert.equal(button('Confirm').disabled, true);
    await view.render(
      render({ rows: [{ ...selected, id: 'loaded' }], isReady: true }),
    );
    assert.equal(item('loaded').getAttribute('aria-selected'), 'true');
    await click(button('Confirm'));
    assert.equal(calls, 1);
    assert.ok(dialog());
    await view.render(
      render({ rows: [selected], isReady: true, error: 'Saving failed' }),
    );
    assert.equal(
      dialog().querySelector('[role=alert]').textContent,
      'Saving failed',
    );
    assert.equal(item('wiki').getAttribute('aria-selected'), 'true');
    assert.equal(button('Confirm').disabled, false);
    await view.render(
      render({ rows: [selected], isReady: true, confirming: true }),
    );
    assert.equal(button('Confirm').disabled, true);
    assert.equal(button('Cancel').disabled, true);
    assert.equal(dialog().querySelector('.ov-modal__close').disabled, true);
  } finally {
    await view.unmount();
  }
});

test('search, footer actions, disabled rows and keyboard selection use host callbacks', async () => {
  let query;
  let changes = 0;
  let actions = 0;
  const rows = [folder('locked', { disabled: true }), folder('wiki')];
  function Host({ disabled = false }) {
    const [value, setValue] = useState([]);
    return h(ContextPicker, {
      open: true,
      autoReveal: false,
      rows,
      value,
      disabled,
      query: 'wi',
      onQueryChange: (next) => {
        query = next;
      },
      onChange: (next) => {
        changes++;
        setValue(next);
      },
      onCancel() {},
      slots: {
        footerStart: h(
          'button',
          {
            onClick: () => {
              actions++;
            },
          },
          'Add skill',
        ),
      },
    });
  }
  const view = await mount(h(Host));
  try {
    await click(item('locked'));
    assert.equal(changes, 0);
    await act(async () =>
      item('wiki').dispatchEvent(
        new window.KeyboardEvent('keydown', {
          key: ' ',
          bubbles: true,
          cancelable: true,
        }),
      ),
    );
    assert.equal(changes, 1);
    assert.equal(item('wiki').getAttribute('aria-selected'), 'true');
    const input = dialog().querySelector('input');
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(input, 'wiki');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    assert.equal(query, 'wiki');
    const clear = dialog().querySelector('[aria-label="Clear search"]');
    assert.equal(clear.getAttribute('aria-label'), 'Clear search');
    assert.equal(clear.closest('[aria-hidden=true]'), null);
    await click(clear);
    assert.equal(query, '');
    await click(button('Add skill'));
    assert.equal(actions, 1);
    await view.render(h(Host, { disabled: true }));
    await click(item('wiki'));
    assert.equal(changes, 1);
    assert.equal(dialog().querySelector('input').disabled, true);
  } finally {
    await view.unmount();
  }
});
