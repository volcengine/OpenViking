import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import { mount } from './helpers/module-dom.mjs';
import { click } from './helpers/acl-dom.mjs';

const source = (folder) => ({
  fileName: 'guide.md',
  path: 'viking://resources/' + folder + '/guide.md',
  content: '',
});
const button = (root = document) =>
  root.querySelector('button[aria-label="Run"]');
function setup() {
  let calls = 0,
    otherCalls = 0,
    resolve,
    reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  const action = {
    key: 'run',
    label: 'Run',
    icon: h('svg'),
    onClick: () => (++calls === 1 ? promise : 42),
  };
  const a = {
    kind: 'file',
    key: 'a',
    source: source('a'),
    actions: [action],
  };
  const b = {
    ...a,
    key: 'b',
    source: source('b'),
    actions: [{ ...action, onClick: () => ++otherCalls }],
  };
  const render = (props = {}) =>
    h(FilePreview, { tabs: [a, b], activeKey: 'a', ...props });
  return {
    a,
    b,
    render,
    resolve,
    reject,
    calls: () => calls,
    otherCalls: () => otherCalls,
  };
}

const variants = [
  'file-tab',
  'file-tab-pathless',
  'content-tab',
  'slot-node',
  'slot-null',
  'slot-function',
  'resource',
  'file-level',
  'directory-level',
  'removed-tab',
  'same-resource-tab',
];
for (const variant of variants)
  for (const fails of [false, true]) {
    test('pending ' + variant + ': ' + fails, async () => {
      const state = setup();
      const { a, b, render } = state;
      const levels = [
        { key: 'l0', label: 'L0', source: a.source },
        { key: 'l1', label: 'L1', source: b.source },
      ];
      if (variant.endsWith('-level')) a.levels = levels;
      if (variant === 'directory-level') a.kind = 'directory';
      if (variant === 'file-tab-pathless')
        a.source.path = b.source.path = undefined;
      let changed;
      if (variant.startsWith('slot-')) {
        const actions =
          variant === 'slot-null'
            ? null
            : variant === 'slot-function'
              ? () => h('span', null, 'Host actions')
              : h('span', null, 'Host actions');
        changed = { tabs: [{ ...a, slots: { actions } }, b] };
      } else if (variant === 'content-tab') {
        changed = {
          tabs: [a, { kind: 'content', key: 'b', content: 'Host body' }],
          activeKey: 'b',
        };
      } else if (variant === 'resource') {
        changed = {
          tabs: [{ ...a, source: b.source, actions: b.actions }, b],
        };
      } else if (variant.endsWith('-level')) {
        changed = { tabs: [{ ...a, activeLevelKey: 'l1' }, b] };
      } else if (variant === 'removed-tab') {
        changed = { tabs: [b], activeKey: 'b' };
      } else if (variant === 'same-resource-tab') {
        changed = {
          tabs: [a, { ...b, source: a.source, actions: a.actions }],
          activeKey: 'b',
        };
      } else changed = { activeKey: 'b' };
      const view = await mount(render());
      try {
        await click(button());
        await view.render(render(changed));
        if (
          variant.startsWith('file-tab') ||
          ['resource', 'removed-tab'].includes(variant)
        ) {
          assert.equal(
            button().disabled,
            false,
            'another resource remains usable',
          );
          await click(button());
          assert.equal(state.otherCalls(), 1);
        } else if (
          variant.endsWith('-level') ||
          variant === 'same-resource-tab'
        ) {
          assert.equal(
            button().disabled,
            true,
            'the resource keeps its running command',
          );
        } else assert.equal(button(), null);
        await view.render(render());
        assert.equal(button().disabled, true);
        await click(button());
        assert.equal(state.calls(), 1);
        await act(async () =>
          fails ? state.reject(new Error('Retry A')) : state.resolve(42),
        );
        assert.equal(button().disabled, false);
        if (fails)
          assert.match(
            document.querySelector('[role="alert"]').textContent,
            /Retry A/,
          );
        await click(button());
        assert.equal(state.calls(), 2);
        assert.equal(document.querySelector('[role="alert"]'), null);
      } finally {
        state.resolve();
        await view.unmount();
      }
    });
  }

for (const fails of [false, true]) {
  test('background completion stays with its resource: ' + fails, async () => {
    const state = setup();
    const view = await mount(state.render());
    try {
      await click(button());
      await view.render(state.render({ activeKey: 'b' }));
      await act(async () =>
        fails ? state.reject(new Error('Failure in A')) : state.resolve(42),
      );
      assert.equal(button().disabled, false);
      assert.equal(document.querySelector('[role="alert"]'), null);
      await view.render(state.render());
      assert.equal(button().disabled, false);
      assert.equal(Boolean(document.querySelector('[role="alert"]')), fails);
      await click(button());
      assert.equal(state.calls(), 2);
      assert.equal(document.querySelector('[role="alert"]'), null);
    } finally {
      state.resolve();
      await view.unmount();
    }
  });
}

test('separate preview instances do not share command locks', async () => {
  const first = setup(),
    second = setup();
  const a = await mount(first.render()),
    b = await mount(second.render());
  try {
    await click(button(a.container));
    assert.equal(button(b.container).disabled, false);
    await click(button(b.container));
    assert.equal(first.calls(), 1);
    assert.equal(second.calls(), 1);
    await act(async () => {
      first.resolve();
      second.resolve();
    });
  } finally {
    first.resolve();
    second.resolve();
    await a.unmount();
    await b.unmount();
  }
});
