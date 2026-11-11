import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import { mount, settle } from './helpers/module-dom.mjs';
const action = (key, extra = {}) => ({
  key,
  label: key,
  icon: h('svg', { 'data-icon': key }),
  onClick: () => key,
  ...extra,
});
const source = {
  content: '',
  fileName: 'guide.md',
  path: 'viking://resources/guide.md',
};
const tree = (actions, extra = {}, props = {}) =>
  h(FilePreview, {
    tabs: [{ kind: 'file', key: 'guide', source, actions, ...extra }],
    ...props,
  });
const button = (label) =>
  [...document.querySelectorAll('button')].find(
    (node) => node.getAttribute('aria-label') === label,
  );
const menu = () => document.querySelector('[role="menu"]');
const click = async (node) => {
  assert.ok(node);
  await act(async () => node.click());
};
const key = async (node, value) => {
  await act(async () =>
    node.dispatchEvent(
      new window.KeyboardEvent('keydown', {
        key: value,
        bubbles: true,
        cancelable: true,
      }),
    ),
  );
};
const pointer = async (node, type) => {
  const event = new window.MouseEvent(type, { bubbles: true });
  Object.defineProperty(event, 'pointerType', { value: 'mouse' });
  await act(async () => node.dispatchEvent(event));
};

test('only view and edit are built in, even when copying/downloading is available', async () => {
  const view = await mount(
    tree(undefined, {
      loading: true,
      source: { ...source, content: '# Guide' },
      operations: { download: async () => {} },
      editing: { save: async () => {} },
    }),
  );
  try {
    assert.ok(button('Edit'));
    for (const label of [
      'Copy path',
      'Copy content',
      'Download',
      'More actions',
    ])
      assert.equal(button(label), undefined);
  } finally {
    await view.unmount();
  }
});

test('zero and one action use no overflow menu; icon-only buttons have a tooltip and host class', async () => {
  const view = await mount(tree([[], []]));
  try {
    assert.equal(button('More actions'), undefined);
    assert.equal(document.querySelector('.ov-file-preview__toolbar'), null);
    await view.render(
      tree([[], action('Run', { className: 'host-command' }), []]),
    );
    const run = button('Run');
    assert.ok(run.classList.contains('host-command'));
    assert.equal(run.textContent, '');
    assert.ok(run.querySelector('[data-icon="Run"]'));
    await act(async () => run.focus());
    await act(async () => new Promise((resolve) => setTimeout(resolve, 350)));
    assert.equal(document.querySelector('[role="tooltip"]').textContent, 'Run');
    assert.equal(button('More actions'), undefined);
  } finally {
    await view.unmount();
  }
});

test('mixed groups keep order, skip empty arrays, and split only at nonempty explicit groups', async () => {
  const view = await mount(
    tree([
      [],
      action('a'),
      [],
      action('b'),
      [action('c')],
      [],
      action('d'),
      action('e'),
      [],
      [action('f')],
      [],
    ]),
  );
  try {
    await click(button('More actions'));
    assert.deepEqual(
      [...menu().querySelectorAll('button')].map((node) => node.textContent),
      ['a', 'b', 'c', 'd', 'e', 'f'],
    );
    assert.equal(menu().querySelectorAll('hr').length, 3);
    await view.render(tree([action('a'), action('b')]));
    assert.equal(menu().querySelectorAll('hr').length, 0);
  } finally {
    await view.unmount();
  }
});

test('hover opens and bridges to menu; keyboard skips disabled items and restores focus', async () => {
  const view = await mount(
    tree([action('a'), [action('b', { disabled: true }), action('c')]]),
  );
  try {
    const trigger = button('More actions');
    await pointer(trigger, 'pointerover');
    assert.ok(menu());
    await pointer(trigger, 'pointerout');
    await pointer(menu(), 'pointerover');
    await act(async () => new Promise((resolve) => setTimeout(resolve, 180)));
    assert.ok(menu());
    await key(trigger, 'ArrowDown');
    assert.equal(document.activeElement.textContent, 'a');
    await key(document.activeElement, 'ArrowDown');
    assert.equal(document.activeElement.textContent, 'c');
    await key(document.activeElement, 'Home');
    assert.equal(document.activeElement.textContent, 'a');
    await key(document.activeElement, 'End');
    assert.equal(document.activeElement.textContent, 'c');
    await key(document.activeElement, 'Escape');
    assert.equal(menu(), null);
    assert.equal(document.activeElement, trigger);
    await key(trigger, 'ArrowUp');
    assert.equal(document.activeElement.textContent, 'c');
    await key(document.activeElement, 'Tab');
    assert.equal(menu(), null);
  } finally {
    await view.unmount();
  }
});

test('new actions take precedence over legacy menus, including an explicit empty array', async () => {
  const legacy = {
    loading: true,
    source: { ...source, content: '# Guide' },
    slots: {
      menuItems: [{ key: 'legacy', label: 'Legacy', onSelect: () => {} }],
    },
  };
  const view = await mount(tree(undefined, legacy));
  try {
    assert.ok(button('More actions'));
    assert.ok(button('Copy path'));
    await view.render(tree([], legacy));
    assert.equal(button('More actions'), undefined);
    assert.equal(button('Copy path'), undefined);
  } finally {
    await view.unmount();
  }
});

test('focus outside a pointer-open menu dismisses it without stealing focus', async () => {
  const view = await mount(
    h(
      'div',
      null,
      h('input', { 'aria-label': 'Outside' }),
      tree([action('a'), action('b')]),
    ),
  );
  try {
    await pointer(button('More actions'), 'pointerover');
    assert.ok(menu());
    const input = view.container.querySelector('input');
    await act(async () => input.focus());
    assert.equal(menu(), null);
    assert.equal(document.activeElement, input);
  } finally {
    await view.unmount();
  }
});
