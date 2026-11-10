import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { FileTree } from '@openviking/react/components/file-tree';

const render = (props = {}) =>
  renderToStaticMarkup(createElement(FileTree, { rows: [], ...props }));
const operations = { search: async () => {}, refresh: async () => {} };

test('toolbar controls are opt-in independently of operation capabilities', () => {
  assert.doesNotMatch(render({ operations }), /ov-file-tree__controls/);
  const search = render({ search: true, operations });
  assert.match(search, /ov-file-tree__search"/);
  assert.doesNotMatch(search, /ov-file-tree__refresh"/);
  const refresh = render({ refresh: true, operations });
  assert.match(refresh, /ov-file-tree__refresh"/);
  assert.doesNotMatch(refresh, /ov-file-tree__search"/);
});

test('missing capabilities and explicit host flags disable the controls', () => {
  for (const props of [
    { search: true, refresh: true },
    { search: { disabled: true }, refresh: { disabled: true }, operations },
  ]) {
    const html = render(props);
    assert.match(html, /class="ov-file-tree__search" disabled=""/);
    assert.match(html, /class="ov-file-tree__refresh"[^>]*disabled=""/);
  }
});

test('host refresh state is accessible and labels are replaceable without browser globals', () => {
  const html = render({
    search: true,
    refresh: { loading: true },
    operations,
    labels: { search: '搜索文件', refresh: '刷新目录' },
  });
  assert.match(html, /搜索文件/);
  assert.match(html, /aria-label="刷新目录"/);
  assert.match(html, /aria-busy="true" disabled=""/);
  assert.match(html, /ov-file-tree__refresh-spinning/);
  assert.doesNotMatch(html, / search=| refresh=/);
});

test('custom toolbar remains available above the built-in controls', () => {
  const html = render({
    search: true,
    operations,
    slots: { toolbar: createElement('button', null, 'Import') },
  });
  assert.ok(html.indexOf('Import') < html.indexOf('ov-file-tree__search'));
});
