import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement } from 'react';

import { FileTree } from '@openviking/react/components/file-tree';
import { VikingBotSessionActions } from '@openviking/react/components/viking-bot';
import { mount } from './helpers/module-dom.mjs';

globalThis.MutationObserver = window.MutationObserver;

test('row titles use entry names and trailing hover suppresses only the row card', async () => {
  const tree = await mount(
    createElement(FileTree, {
      rows: ['directory', 'file'].map((type) => ({
        kind: 'entry',
        id: type,
        type,
        depth: 0,
        uri: `viking://resources/${type}`,
        name: `${type} name`,
      })),
      renderRowTooltip: (row) => `Information: ${row.name}`,
      renderRowTrailing: () => createElement('button', null, 'Dashboard'),
    }),
  );
  const hover = async (from, to) => {
    await act(async () => {
      from?.dispatchEvent(
        new window.MouseEvent('mouseout', {
          bubbles: true,
          relatedTarget: to,
        }),
      );
      to?.dispatchEvent(
        new window.MouseEvent('mouseover', {
          bubbles: true,
          relatedTarget: from,
        }),
      );
      await new Promise((resolve) => setTimeout(resolve, 330));
    });
  };
  try {
    const rows = tree.container.querySelectorAll('[role="treeitem"]');
    for (const [index, type] of ['directory', 'file'].entries()) {
      assert.equal(rows[index].title, `${type} name`);
    }
    const row = rows[0];
    const label = row.querySelector('.ov-file-tree__label');
    const button = row.querySelector('.ov-file-tree__trailing button');
    assert.equal(button.closest('[title]').title, '');
    await hover(null, button);
    assert.equal(document.querySelector('.ov-popover'), null);
    await hover(button, label);
    assert.equal(
      document.querySelector('.ov-popover')?.textContent,
      'Information: directory name',
    );
    await hover(label, button);
    assert.equal(document.querySelector('.ov-popover'), null);
    await hover(button, document.body);
    assert.equal(document.querySelector('.ov-popover'), null);
    await hover(document.body, label);
    assert.ok(document.querySelector('.ov-popover'));
  } finally {
    await tree.unmount();
  }
});

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
    const button = anchor.querySelector('button');
    button.getBoundingClientRect = () => new window.DOMRect(28, 60, 625, 32);
    await act(() => button.click());
    const surface = document
      .getElementById(button.getAttribute('aria-controls'))
      .closest('.ov-dropdown');
    surface.getBoundingClientRect = () => new window.DOMRect(0, 0, 200, 80);
    await act(() => window.dispatchEvent(new window.Event('resize')));
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
