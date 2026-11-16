import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderMarkup } from './helpers/csr-render.mjs';
import { FileTree } from '@openviking/react/components/file-tree';

const render = (props = {}) =>
  renderMarkup(
    createElement(FileTree, { rows: [], locale: 'en-US', ...props }),
  );
const operations = { search: async () => {}, refresh: async () => {} };

test('toolbar controls are visible by default and can be hidden independently', () => {
  const defaults = render({ operations });
  assert.match(defaults, /ov-file-tree__search"/);
  assert.match(defaults, /ov-file-tree__refresh"/);
  assert.doesNotMatch(
    render({ search: false, refresh: false, operations }),
    /ov-file-tree__controls/,
  );
  const search = render({ refresh: false, operations });
  assert.match(search, /ov-file-tree__search"/);
  assert.doesNotMatch(search, /ov-file-tree__refresh"/);
  const refresh = render({ search: false, operations });
  assert.match(refresh, /ov-file-tree__refresh"/);
  assert.doesNotMatch(refresh, /ov-file-tree__search"/);
});

test('missing capabilities and explicit host flags disable the controls', () => {
  for (const props of [
    { search: true, refresh: true },
    { search: { disabled: true }, refresh: { disabled: true }, operations },
  ]) {
    const html = render(props);
    assert.match(
      html,
      /<button[^>]*disabled=""[^>]*class="[^"]*ov-file-tree__search"/,
    );
    assert.match(
      html,
      /<button[^>]*disabled=""[^>]*class="[^"]*ov-file-tree__refresh"/,
    );
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
  assert.match(html, /aria-busy="true"[^>]*disabled=""/);
  assert.match(html, /ov-button__spinner/);
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

test('built-in toolbar copy follows locale and preserves individual host overrides', () => {
  const chinese = render({
    locale: 'zh-CN',
    search: true,
    refresh: true,
    operations,
  });
  assert.match(chinese, /搜索文件 ⌘\+K\.\.\./);
  assert.match(chinese, /aria-label="刷新文件树"/);
  const english = render({ search: true, refresh: true, operations });
  assert.match(english, /Search files ⌘\+K\.\.\./);
  assert.match(english, /aria-label="Refresh file tree"/);
  const overridden = render({
    locale: 'zh-CN',
    search: true,
    refresh: true,
    operations,
    labels: { search: 'Host search' },
  });
  assert.match(overridden, /Host search/);
  assert.match(overridden, /aria-label="刷新文件树"/);
});

test('top-level loading controls refresh without leaking onto the root DOM', () => {
  const html = render({ loading: true, operations });
  assert.match(html, /aria-busy="true"[^>]*disabled=""/);
  assert.match(html, /ov-button__spinner/);
  assert.doesNotMatch(html, / loading=/);
});
