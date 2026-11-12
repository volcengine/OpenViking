import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { DirectoryTour } from '@openviking/react/components/directory-tour';
import { mount } from './helpers/module-dom.mjs';

const step = {
  id: 'step',
  target: 'target',
  title: 'Title',
  description: 'Description',
};
const panel = () => document.querySelector('.ov-directory-tour');

test('on narrow screens the card stays clear of the highlighted row', async () => {
  const anchor = document.createElement('div');
  anchor.scrollIntoView = () => {};
  document.body.append(anchor);
  const prototype = window.HTMLElement.prototype;
  const originalRect = prototype.getBoundingClientRect;
  const { innerWidth, innerHeight } = window;
  window.innerWidth = 390;
  window.innerHeight = 700;
  let top = 100;
  anchor.getBoundingClientRect = () => ({
    left: 24,
    right: 366,
    top,
    bottom: top + 32,
    width: 342,
    height: 32,
  });
  prototype.getBoundingClientRect = function () {
    return this.classList.contains('ov-directory-tour')
      ? { width: 270, height: 222 }
      : originalRect.call(this);
  };
  const view = await mount(
    h(DirectoryTour, {
      open: true,
      steps: [step],
      locateTarget: async () => anchor,
    }),
  );
  try {
    assert.ok(parseFloat(panel().parentElement.style.top) >= 132);
    top = 600;
    await act(async () => window.dispatchEvent(new window.Event('resize')));
    assert.ok(parseFloat(panel().parentElement.style.top) + 222 <= top);
  } finally {
    await view.unmount();
    anchor.remove();
    prototype.getBoundingClientRect = originalRect;
    Object.assign(window, { innerWidth, innerHeight });
  }
});
