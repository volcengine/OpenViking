import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, useState } from 'react';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import { mount } from './helpers/module-dom.mjs';

const root = 'viking://resources';
const wiki = `${root}/wiki`;
const selected = `${wiki}/archive`;
const entry = (uri, depth, expanded, hasChildren = false) => ({
  kind: 'entry',
  type: 'directory',
  uri,
  id: uri,
  depth,
  name: uri.split('/').at(-1),
  expanded,
  hasChildren,
});
const readDirectory = async (uri) => ({
  uri,
  isDirectory: true,
  canWrite: true,
});

test('opening a nested target expands only its ancestors, loads missing pages and scrolls to the restored selection', async () => {
  const actions = [];
  function Host() {
    const [expanded, setExpanded] = useState(new Set());
    const [page, setPage] = useState(false);
    const rows = [entry(root, 0, expanded.has(root), true)];
    if (expanded.has(root)) {
      rows.push(entry(`${root}/compiled`, 1, false));
      if (page) {
        rows.push(entry(wiki, 1, expanded.has(wiki), true));
        if (expanded.has(wiki)) rows.push(entry(selected, 2, false));
      } else
        rows.push({ kind: 'load-more', id: 'more', parentId: root, depth: 1 });
    }
    return h(VikingBotInteraction, {
      request: {
        kind: 'target',
        initialUri: selected,
        directoryPicker: {
          rows,
          operations: {
            readDirectory,
            toggleExpand: (row) => {
              actions.push(row.uri);
              setExpanded((value) => new Set([...value, row.uri]));
              return Promise.resolve();
            },
            loadMore: () => {
              actions.push('page');
              setPage(true);
              return Promise.resolve();
            },
          },
        },
      },
    });
  }
  const view = await mount(h(Host));
  const oldCSS = globalThis.CSS;
  globalThis.CSS = {
    escape: (value) => value.replaceAll(':', '\\:').replaceAll('/', '\\/'),
  };
  const scrolled = [];
  window.HTMLElement.prototype.scrollIntoView = function () {
    scrolled.push(this.getAttribute('data-file-tree-row-id'));
  };
  try {
    await act(async () =>
      view.container.querySelector('.ov-viking-bot__directory-trigger').click(),
    );
    await act(async () => new Promise((resolve) => setTimeout(resolve, 350)));
    assert.deepEqual(actions, [root, 'page', wiki]);
    assert.deepEqual(scrolled, [selected]);
    const chosen = [...document.querySelectorAll('[role=treeitem]')].find(
      (node) => node.textContent === 'archive',
    );
    assert.equal(chosen.getAttribute('aria-selected'), 'true');
    const cancel = [
      ...document.querySelectorAll('.ov-context-picker button'),
    ].find((node) => node.textContent === 'Cancel');
    await act(async () => cancel.click());
  } finally {
    await view.unmount();
    globalThis.CSS = oldCSS;
  }
});
