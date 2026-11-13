import assert from 'node:assert/strict';
import test from 'node:test';

import {
  toDirectoryTab,
  toFileTab,
} from '../src/modules/file-browser/fs-model.ts';

test('a file tab shows the file name, not the whole URI', () => {
  const tab = toFileTab(
    { uri: 'viking://user/default/memories/profile.md' },
    'body',
    2000,
    null,
  );
  assert.equal(tab.title, 'profile.md');
  assert.equal(tab.source.fileName, 'profile.md');
  // 完整路径仍留在 source 上，标题行的面包屑要用它。
  assert.equal(tab.source.path, 'viking://user/default/memories/profile.md');
});

test('a directory tab shows the last segment, not the whole URI', () => {
  const tab = toDirectoryTab(
    'viking://user/default/memories',
    { abstract: 'a', overview: 'o' },
    { l0: 'L0', l1: 'L1' },
  );
  assert.equal(tab.title, 'memories');
  assert.equal(tab.kind, 'directory');
});

test('a tree row keeps its own aria-expanded under the hover popover', async () => {
  // 行的 `aria-expanded` 表达「目录展开与否」；浮层可见性是另一件事。两者混在
  // 一起会让读屏器读到错误的树结构：目录行恒报 false，文件行凭空多出该属性。
  //
  // A row's `aria-expanded` means "directory expanded or not"; popover visibility is
  // a separate thing. Merging them gives assistive technology the wrong tree
  // structure: directory rows always report false and file rows gain the attribute.
  const { mount } = await import('./helpers/module-dom.mjs');
  const { FileTree } = await import('@openviking/react/components/file-tree');
  const { createElement } = await import('react');

  const rows = [
    {
      kind: 'entry',
      id: 'a',
      uri: 'viking://a',
      name: 'a',
      depth: 0,
      type: 'directory',
      expanded: true,
    },
    {
      kind: 'entry',
      id: 'b.md',
      uri: 'viking://b.md',
      name: 'b.md',
      depth: 1,
      type: 'file',
    },
  ];
  const tree = await mount(createElement(FileTree, { rows, isReady: true }));
  const nodes = [...tree.container.querySelectorAll('[role="treeitem"]')];
  assert.equal(nodes.length, 2);
  // 目录行保留真实展开态；文件行完全没有该属性。
  // The directory keeps its real expansion; the file has no attribute at all.
  assert.equal(nodes[0].getAttribute('aria-expanded'), 'true');
  assert.equal(nodes[1].hasAttribute('aria-expanded'), false);
  await tree.unmount();
});

test('a selected file shows loading instead of the no-selection illustration before hydration', async () => {
  const { FileBrowser } =
    await import('@openviking/react/modules/file-browser');
  const { createElement } = await import('react');
  const { renderToStaticMarkup } = await import('react-dom/server');
  const html = renderToStaticMarkup(
    createElement(FileBrowser, { uri: 'viking://resources/guide.md' }),
  );
  assert.match(html, /ov-file-browser__hint[^>]*role="status"[^>]*>Loading…/);
  assert.doesNotMatch(html, /ov-file-browser__empty-art/);
});
