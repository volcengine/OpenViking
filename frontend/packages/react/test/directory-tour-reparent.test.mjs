import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { DirectoryTour } from '@openviking/react/components/directory-tour';
import { mount, settle } from './helpers/module-dom.mjs';

globalThis.MutationObserver = window.MutationObserver;
HTMLElement.prototype.scrollIntoView = () => {};

test('theme follows an adopted ancestry and observes later changes there', async () => {
  const css = document.createElement('style');
  css.textContent = `
    [data-theme="blue"] .tour-target { --ov-ui-color-primary: blue; }
    [data-theme="green"] .tour-target { --ov-ui-color-primary: green; }
    [data-theme="red"] .tour-target { --ov-ui-color-primary: red; }
  `;
  const first = document.createElement('section');
  const second = document.createElement('section');
  first.dataset.theme = 'blue';
  second.dataset.theme = 'green';
  const branch = document.createElement('div');
  const anchor = document.createElement('div');
  anchor.getBoundingClientRect = () => new window.DOMRect(100, 100, 120, 32);
  anchor.className = 'tour-target';
  branch.append(anchor);
  first.append(branch);
  document.head.append(css);
  document.body.append(first, second);

  const view = await mount(
    h(DirectoryTour, {
      open: true,
      steps: [
        { id: 'one', target: 'one', title: 'Guide', description: 'Details' },
      ],
      locateTarget: () => Promise.resolve(anchor),
    }),
  );
  try {
    const dialog = document.querySelector('.ov-directory-tour');
    const primary = () =>
      dialog.parentElement.style.getPropertyValue('--ov-ui-color-primary');
    assert.equal(primary(), 'blue');

    await act(async () => second.append(branch));
    await settle();
    assert.equal(primary(), 'green');

    await act(async () => {
      second.dataset.theme = 'red';
    });
    await settle();
    assert.equal(primary(), 'red');

    await act(async () => {
      branch.remove();
      first.append(branch);
    });
    await settle();
    assert.equal(primary(), 'blue');
    assert.equal(document.querySelector('.ov-directory-tour'), dialog);
  } finally {
    await view.unmount();
    first.remove();
    second.remove();
    css.remove();
  }
});
