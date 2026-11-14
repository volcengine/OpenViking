import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, useState } from 'react';
import { ContextPicker } from '@openviking/react/components/context-picker';
import { mount } from './helpers/module-dom.mjs';

test('multiple opening selections reveal a selected parent and its selected child independently', async () => {
  const root = {
    kind: 'entry',
    type: 'directory',
    uri: 'viking://resources',
    name: 'resources',
    id: 'root',
    depth: 0,
    hasChildren: true,
  };
  const child = {
    ...root,
    uri: 'viking://resources/wiki',
    name: 'wiki',
    id: 'child',
    depth: 1,
    hasChildren: false,
  };
  const expanded = [];
  const previousCSS = globalThis.CSS;
  const previousScroll = window.HTMLElement.prototype.scrollIntoView;
  globalThis.CSS = { escape: (value) => value };
  const scrolled = [];
  window.HTMLElement.prototype.scrollIntoView = function () {
    scrolled.push(this.getAttribute('data-file-tree-row-id'));
  };
  function Host() {
    const [open, setOpen] = useState(false);
    return h(ContextPicker, {
      open: true,
      multiple: true,
      value: [root, child],
      rows: open ? [{ ...root, expanded: true }, child] : [root],
      onCancel() {},
      operations: {
        toggleExpand: (row) => {
          expanded.push(row.id);
          setOpen(true);
          return Promise.resolve();
        },
      },
    });
  }
  const view = await mount(h(Host));
  try {
    await act(async () => new Promise((resolve) => setTimeout(resolve, 350)));
    assert.deepEqual(expanded, ['root']);
    assert.equal(
      document.querySelectorAll('[role=treeitem][aria-selected=true]').length,
      2,
    );
    assert.deepEqual(scrolled, ['root']);
  } finally {
    await view.unmount();
    globalThis.CSS = previousCSS;
    window.HTMLElement.prototype.scrollIntoView = previousScroll;
  }
});
