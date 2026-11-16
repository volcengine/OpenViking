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

test('a wide scrolled row keeps the card beside its visible edge', async () => {
  const scroller = document.createElement('div');
  scroller.style.overflowX = 'auto';
  scroller.style.overflowY = 'auto';
  scroller.getBoundingClientRect = () => new window.DOMRect(310, 60, 260, 200);
  Object.defineProperties(scroller, {
    clientLeft: { value: 2 },
    clientTop: { value: 2 },
    clientWidth: { value: 242 },
    clientHeight: { value: 180 },
  });
  const anchor = document.createElement('div');
  anchor.scrollIntoView = () => {};
  anchor.getBoundingClientRect = () =>
    new window.DOMRect(312 - scroller.scrollLeft, 100, 400, 32);
  scroller.append(anchor);
  document.body.append(scroller);
  const prototype = window.HTMLElement.prototype;
  const originalRect = prototype.getBoundingClientRect;
  prototype.getBoundingClientRect = function () {
    return this.classList.contains('ov-directory-tour')
      ? new window.DOMRect(0, 0, 270, 160)
      : originalRect.call(this);
  };
  const props = { open: true, steps: [step], locateTarget: async () => anchor };
  const view = await mount(h(DirectoryTour, props));
  try {
    const highlight = panel().querySelector('.ov-directory-tour__highlight');
    const assertAligned = (cardLeft) => {
      assert.equal(panel().parentElement.style.left, `${cardLeft}px`);
      assert.equal(highlight.style.left, '312px');
      assert.equal(highlight.style.width, '242px');
    };
    assertAligned(560);
    await act(async () => {
      scroller.scrollLeft = 90;
      scroller.dispatchEvent(new window.Event('scroll'));
    });
    assertAligned(560);
    await view.render(h(DirectoryTour, { ...props, placement: 'left' }));
    assertAligned(36);
  } finally {
    await view.unmount();
    scroller.remove();
    prototype.getBoundingClientRect = originalRect;
  }
});

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
