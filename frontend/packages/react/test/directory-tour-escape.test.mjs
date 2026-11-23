import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import {
  DirectoryTour,
  getOpenVikingDirectoryTourSteps,
} from '@openviking/react/components/directory-tour';
import { ScopeSelector } from '@openviking/react/components/scope-switcher';
import { mount } from './helpers/module-dom.mjs';

globalThis.MutationObserver = window.MutationObserver;
HTMLElement.prototype.scrollIntoView = () => {};

test('a selector opened after the tour claims Escape before the tour closes', async () => {
  const anchor = document.createElement('button');
  anchor.getBoundingClientRect = () => new window.DOMRect(100, 100, 120, 32);
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

test('the localized preset advances all six cards without a close button', async () => {
  const preset = getOpenVikingDirectoryTourSteps('zh-CN');
  const anchor = document.createElement('div');
  anchor.getBoundingClientRect = () => new window.DOMRect(100, 100, 120, 32);
  document.body.append(anchor);
  const panel = () => document.querySelector('.ov-directory-tour');
  const next = () =>
    act(async () =>
      panel().querySelector('.ov-directory-tour__primary').click(),
    );
  const results = [];
  let closed = 0;
  const view = await mount(
    h(DirectoryTour, {
      open: true,
      locale: 'zh-CN',
      steps: preset,
      locateTarget: async () => anchor,
      onClose: () => closed++,
      onComplete: (result) => results.push(result),
    }),
  );
  try {
    for (const [index, step] of preset.entries()) {
      assert.equal(panel().querySelector('h2').textContent, step.title);
      assert.equal(
        panel().querySelector('.ov-directory-tour__description').textContent,
        step.description,
      );
      assert.equal(panel().querySelector('.ov-directory-tour__close'), null);
      assert.equal(
        panel().querySelector('.ov-directory-tour__primary').textContent,
        index === 5 ? '知道了' : '下一步',
      );
      await next();
    }
    assert.equal(panel(), null);
    assert.equal(closed, 0);
    assert.deepEqual(results, [{ skippedStepIds: [] }]);
  } finally {
    await view.unmount();
    anchor.remove();
  }
});
