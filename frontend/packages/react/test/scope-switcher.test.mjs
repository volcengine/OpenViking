import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  ScopeSwitcher,
  ScopeSelector,
} from '@openviking/react/components/scope-switcher';

const level = {
  kind: 'selectable',
  key: 'library',
  selectedId: 'one',
  selectedLabel: 'Current library',
  options: [
    { id: 'one', label: 'One' },
    { id: 'two', label: 'Two', disabled: true },
  ],
  visible: true,
};
const render = (overrides = {}) =>
  renderToStaticMarkup(
    createElement(
      ScopeSwitcher,
      null,
      createElement(ScopeSelector, { level: { ...level, ...overrides } }),
    ),
  );

test('renders selected and disabled options without browser globals or API dependencies', () => {
  const html = render();
  assert.match(html, /role="option" aria-selected="true"/);
  assert.match(html, /aria-disabled="true"[^>]*>.*Two/);
  assert.match(html, /Current library/);
});

test('loading and failure hide stale options and support host recovery content', () => {
  assert.doesNotMatch(render({ loading: true }), /role="option"/);
  assert.match(render({ loading: true }), /Loading/);
  const html = render({
    error: true,
    errorFallback: createElement('button', null, 'Retry request'),
  });
  assert.match(html, /role="alert"/);
  assert.match(html, /Retry request/);
  assert.doesNotMatch(html, /role="option"/);
});

test('search requires a callback and respects total or an active query', () => {
  assert.doesNotMatch(render({ total: 30 }), /type="search"/);
  assert.doesNotMatch(
    render({ total: 10, onSearchKeywordChange() {} }),
    /type="search"/,
  );
  assert.match(
    render({ total: 11, onSearchKeywordChange() {} }),
    /type="search"/,
  );
  assert.match(
    render({ searchKeyword: 'on', onSearchKeywordChange() {} }),
    /<mark>On<\/mark>e/,
  );
});

test('busy disables all options; disabled and fixed levels have no panel', () => {
  assert.equal(
    (render({ busy: true }).match(/aria-disabled="true"/g) ?? []).length,
    2,
  );
  assert.doesNotMatch(render({ disabled: true }), /role="dialog"/);
  const html = renderToStaticMarkup(
    createElement(
      ScopeSwitcher,
      null,
      createElement(ScopeSelector, {
        level: {
          kind: 'fixed',
          key: 'library',
          selectedLabel: 'Fixed library',
        },
      }),
    ),
  );
  assert.match(html, /Fixed library/);
  assert.doesNotMatch(html, /<button|role="dialog"/);
});

test('panel and row slots compose with default contents', () => {
  const html = render({
    renderPanel: ({ list }) =>
      createElement('section', null, 'Libraries', list),
    renderOption: (content) =>
      createElement('span', null, content, 'Custom suffix'),
  });
  assert.match(html, /<section>Libraries/);
  assert.match(html, /Custom suffix/);
});
