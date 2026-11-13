import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  ScopeSwitcher,
  ScopeSelector,
} from '@openviking/react/components/scope-switcher';

const identity = { key: 'library' };
const trigger = { label: 'Current library' };
const data = {
  selectedId: 'one',
  options: [
    { id: 'one', label: 'One' },
    { id: 'two', label: 'Two', disabled: true },
  ],
};
const state = { visible: true };
/** Renders one selectable level, with any group overridden per test. */
const render = ({ data: d, state: st, actions, slots } = {}) =>
  renderToStaticMarkup(
    createElement(
      ScopeSwitcher,
      null,
      createElement(ScopeSelector, {
        kind: 'selectable',
        identity,
        trigger,
        data: { ...data, ...d },
        state: { ...state, ...st },
        actions,
        slots,
      }),
    ),
  );

test('renders selected and disabled options without browser globals or API dependencies', () => {
  const html = render();
  assert.match(html, /role="option" aria-selected="true"/);
  assert.match(html, /aria-disabled="true"[^>]*>.*Two/);
  assert.match(html, /Current library/);
});

test('loading and failure hide stale options and support host recovery content', () => {
  assert.doesNotMatch(render({ state: { loading: true } }), /role="option"/);
  assert.match(render({ state: { loading: true } }), /Loading/);
  const html = render({
    state: { error: true },
    slots: { errorFallback: createElement('button', null, 'Retry request') },
  });
  assert.match(html, /role="alert"/);
  assert.match(html, /Retry request/);
  assert.doesNotMatch(html, /role="option"/);
});

test('search requires a callback and respects total or an active query', () => {
  assert.doesNotMatch(render({ data: { total: 30 } }), /type="search"/);
  assert.doesNotMatch(
    render({ data: { total: 10 }, actions: { onSearchKeywordChange() {} } }),
    /type="search"/,
  );
  assert.match(
    render({ data: { total: 11 }, actions: { onSearchKeywordChange() {} } }),
    /type="search"/,
  );
  assert.match(
    render({
      state: { searchKeyword: 'on' },
      actions: { onSearchKeywordChange() {} },
    }),
    /<mark>On<\/mark>e/,
  );
});

test('every keyword hit is highlighted, case-insensitively', () => {
  const html = render({
    data: { options: [{ id: 'one', label: 'One and one' }] },
    state: { searchKeyword: 'ONE' },
    actions: { onSearchKeywordChange() {} },
  });
  assert.equal((html.match(/<mark>/g) ?? []).length, 2);
  assert.match(html, /<mark>One<\/mark> and <mark>one<\/mark>/);
});

test('the component renders the selection it is given, without normalising it', () => {
  const options = [
    { id: 'a', label: 'Alpha' },
    { id: 'b', label: 'Beta', disabled: true },
    { id: 'c', label: 'Gamma' },
  ];
  // Normalising an invalid selection changes the scope and its requests, so it
  // belongs to whoever owns the selection. The view must not invent one: an
  // empty or unknown id marks nothing, and the trigger keeps the host's label.
  for (const selectedId of ['', 'missing']) {
    const html = render({ data: { options, selectedId } });
    assert.doesNotMatch(html, /aria-selected="true"/);
  }
  // A valid id is rendered as given.
  assert.match(render({ data: { options, selectedId: 'c' } }), /Gamma/);
  // A disabled match keeps both flags: disabled blocks interaction, it does not
  // clear the selection. The host is expected to normalise it away.
  const disabled = render({ data: { options, selectedId: 'b' } });
  assert.match(disabled, /aria-selected="true"[^>]*aria-disabled="true"/);
});

test('a paged subset still shows search when the full set crosses the threshold', () => {
  const options = Array.from({ length: 20 }, (_, i) => ({
    id: String(i),
    label: `Library ${i}`,
  }));
  const html = render({
    data: { options, selectedId: '0', visibleOptions: options.slice(0, 5) },
    actions: { onSearchKeywordChange() {} },
  });
  assert.match(html, /type="search"/);
});

test('a selection filtered out of view stays selected', () => {
  const options = [
    { id: 'a', label: 'Alpha' },
    { id: 'b', label: 'Beta' },
  ];
  // 'a' is selected but the search only shows 'b'. It is still valid, so the
  // panel must not mark 'b' as selected or fall back to it.
  const html = render({
    data: { options, selectedId: 'a', visibleOptions: [options[1]] },
    state: { searchKeyword: 'bet' },
    actions: { onSearchKeywordChange() {} },
  });
  assert.match(html, /Beta/);
  assert.doesNotMatch(html, /aria-selected="true"/);
});

test('busy disables all options; disabled and fixed levels have no panel', () => {
  assert.equal(
    (render({ state: { busy: true } }).match(/aria-disabled="true"/g) ?? [])
      .length,
    2,
  );
  assert.doesNotMatch(render({ state: { disabled: true } }), /role="dialog"/);
  const html = renderToStaticMarkup(
    createElement(
      ScopeSwitcher,
      null,
      createElement(ScopeSelector, {
        kind: 'fixed',
        identity: { key: 'library' },
        trigger: { label: 'Fixed library' },
      }),
    ),
  );
  assert.match(html, /Fixed library/);
  assert.doesNotMatch(html, /<button|role="dialog"/);
});

test('a host-owned panel is marked so it shares the default height cap', () => {
  const custom = render({
    slots: {
      renderPanel: ({ list }) => createElement('section', null, list),
    },
  });
  assert.match(custom, /ov-scope-switcher__panel--custom/);
  assert.doesNotMatch(render(), /ov-scope-switcher__panel--custom/);
});

test('panel and row slots compose with default contents', () => {
  const html = render({
    slots: {
      renderPanel: ({ list }) =>
        createElement('section', null, 'Libraries', list),
      renderOption: (content) =>
        createElement('span', null, content, 'Custom suffix'),
    },
  });
  assert.match(html, /<section>Libraries/);
  assert.match(html, /Custom suffix/);
});
