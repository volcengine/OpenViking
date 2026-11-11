import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement } from 'react';

import { FileTree } from '@openviking/react/components/file-tree';
import { VikingBotSessionActions } from '@openviking/react/components/viking-bot';
import { mount } from './helpers/module-dom.mjs';

globalThis.MutationObserver = window.MutationObserver;

test('a wide tree row anchors its popover to the visible scrollport', async () => {
  const tree = await mount(
    createElement(
      'div',
      { style: { overflowX: 'auto' } },
      createElement(FileTree, {
        rows: [
          {
            kind: 'entry',
            id: 'memories',
            uri: 'viking://user/default/memories',
            name: 'memories',
            type: 'directory',
            depth: 2,
          },
        ],
      }),
    ),
  );
  try {
    const scrollport = tree.container.firstElementChild;
    scrollport.getBoundingClientRect = () =>
      new window.DOMRect(28, 20, 336, 300);
    Object.defineProperties(scrollport, {
      clientWidth: { value: 336 },
      clientHeight: { value: 300 },
    });
    const row = scrollport.querySelector('[role="treeitem"]');
    let scrollLeft = 0;
    row.getBoundingClientRect = () =>
      new window.DOMRect(28 - scrollLeft, 60, 625, 32);
    row.querySelector('.ov-file-tree__popover-anchor').getBoundingClientRect =
      () => new window.DOMRect(364, 60, 0, 32);

    await act(async () => {
      row.dispatchEvent(new window.MouseEvent('mouseover', { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 330));
    });
    const surface = document.querySelector('.ov-popover');
    assert.ok(surface, 'hovering a directory must open its information card');
    surface.getBoundingClientRect = () => new window.DOMRect(0, 0, 200, 80);
    await act(() => window.dispatchEvent(new window.Event('resize')));

    assert.equal(surface.style.left, '370px');
    assert.equal(surface.getAttribute('data-placement'), 'right');

    scrollLeft = 120;
    await act(() => scrollport.dispatchEvent(new window.Event('scroll')));
    assert.equal(
      surface.style.left,
      '370px',
      'horizontal scrolling must keep the card beside the visible row edge',
    );
  } finally {
    await tree.unmount();
  }
});

test('other popovers and tooltips retain the full trigger geometry', async () => {
  const tree = await mount(
    createElement(
      'div',
      { style: { overflowX: 'auto' } },
      createElement(VikingBotSessionActions, {
        sessions: { items: [], onSelect() {}, onNew() {} },
      }),
    ),
  );
  try {
    const scrollport = tree.container.firstElementChild;
    scrollport.getBoundingClientRect = () =>
      new window.DOMRect(28, 20, 336, 300);
    Object.defineProperties(scrollport, {
      clientWidth: { value: 336 },
      clientHeight: { value: 300 },
    });
    const anchor = scrollport.querySelector('.ov-viking-bot__popover-anchor');
    anchor.getBoundingClientRect = () => new window.DOMRect(28, 60, 625, 32);
    const button = anchor.querySelector('button');
    const surface = document.getElementById(
      button.getAttribute('aria-controls'),
    );
    surface.getBoundingClientRect = () => new window.DOMRect(0, 0, 200, 80);
    await act(() => button.click());
    assert.equal(button.getAttribute('aria-expanded'), 'true');
    assert.equal(surface.style.left, '453px');

    await act(() => button.click());
    const newSession = scrollport.querySelectorAll('button')[1];
    newSession.getBoundingClientRect = () =>
      new window.DOMRect(28, 60, 625, 32);
    await act(async () => {
      newSession.dispatchEvent(
        new window.MouseEvent('mouseover', { bubbles: true }),
      );
      await new Promise((resolve) => setTimeout(resolve, 330));
    });
    const tooltip = document.querySelector('[role="tooltip"]');
    assert.ok(tooltip);
    tooltip.getBoundingClientRect = () => new window.DOMRect(0, 0, 200, 80);
    await act(() => window.dispatchEvent(new window.Event('resize')));
    assert.equal(tooltip.style.left, '240.5px');
  } finally {
    await tree.unmount();
  }
});
