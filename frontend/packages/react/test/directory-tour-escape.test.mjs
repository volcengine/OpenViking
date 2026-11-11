import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { DirectoryTour } from '@openviking/react/components/directory-tour';
import { ScopeSelector } from '@openviking/react/components/scope-switcher';
import { mount } from './helpers/module-dom.mjs';

globalThis.MutationObserver = window.MutationObserver;
HTMLElement.prototype.scrollIntoView = () => {};

test('a selector opened after the tour claims Escape before the tour closes', async () => {
  const anchor = document.createElement('button');
  document.body.append(anchor);
  let closed = 0;
  const view = await mount(
    h(DirectoryTour, {
      open: true,
      steps: [{ id: 'one', target: 'one', title: 'Guide', description: '' }],
      locateTarget: () => Promise.resolve(anchor),
      onClose: () => closed++,
      renderContent: () =>
        h(ScopeSelector, {
          kind: 'selectable',
          identity: { key: 'library' },
          trigger: { label: 'Current library' },
          data: { options: [{ id: 'one', label: 'One' }] },
        }),
    }),
  );
  try {
    const trigger = document.querySelector('[role="dialog"] button');
    assert.ok(trigger);
    await act(async () => trigger.click());
    assert.ok(document.querySelector('[role="option"]'));

    const escape = () =>
      new window.KeyboardEvent('keydown', {
        key: 'Escape',
        bubbles: true,
        cancelable: true,
      });
    await act(async () => document.activeElement.dispatchEvent(escape()));
    assert.equal(document.querySelector('[role="option"]'), null);
    assert.equal(closed, 0);
    assert.equal(document.activeElement, trigger);

    await act(async () => trigger.dispatchEvent(escape()));
    assert.equal(closed, 1);
  } finally {
    await view.unmount();
    anchor.remove();
  }
});
