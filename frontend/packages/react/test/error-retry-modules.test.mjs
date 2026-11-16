import assert from 'node:assert/strict';
import { test } from 'node:test';
import { act, createElement as h } from 'react';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { Console } from '@openviking/react/modules/console';
import { OpenVikingProvider } from '@openviking/react/client';
import { mount, settle } from './helpers/module-dom.mjs';
import { fault, retrySlot, verifyRetry } from './helpers/error-retry.mjs';
import { tree, fixture, target } from './helpers/preview-native-dom.mjs';

const internal = (name) =>
  import(pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, `${name}.js`)));
const { ErrorBoundary } = await internal('error-boundary');
const { FileBrowserTreePane } = await internal('browser-tree-pane');
const { FileBrowserPreviewPane } = await internal('browser-preview-pane');
const { FileBrowserSearch } = await internal('browser-search');
const { resolveLabels } = await internal('browser-labels');
const { OpenVikingProvider: PrivateProvider } = await internal('provider');
const noop = () => {};
const fetch = fixture(target, false);
const error = (f) =>
  Object.defineProperty(new Error(), 'message', { get: f.read });
const wrap = (slots, content, transport = fetch) =>
  h(
    PrivateProvider,
    { fetch: transport },
    h(
      ErrorBoundary,
      { fallback: slots.errorFallback },
      h('span', { 'data-stable-sibling': true }, 'Healthy sibling'),
      content,
    ),
  );
const browser = () => ({
  rows: [],
  dashboards: new Map(),
  isReady: true,
  actions: {
    refresh: async () => {},
    select: noop,
    toggleExpand: noop,
    loadMore: noop,
    retry: noop,
    setPreviewKey: noop,
  },
});

for (const [name, view, preserve] of [
  [
    'FileBrowser outer boundary',
    (f, slots) =>
      tree(fetch, target, { slots, className: { toString: f.read } }),
  ],
  [
    'Console outer boundary',
    (f, slots) =>
      h(
        OpenVikingProvider,
        { fetch },
        h(Console, {
          slots,
          defaultMode: 'command',
          className: { toString: f.read },
        }),
      ),
  ],
  ...['toolbar', 'commandHelp'].map((region) => [
    `Console ${region}`,
    (f, slots) =>
      h(
        OpenVikingProvider,
        { fetch },
        h(Console, {
          defaultMode: 'command',
          slots: { ...slots, [region]: h(f.Content) },
        }),
      ),
    region === 'toolbar' ? '.ov-command-console' : '.ov-console__toolbar',
  ]),
  [
    'Console conversation',
    (f, slots) =>
      h(
        OpenVikingProvider,
        { fetch },
        h(Console, {
          slots,
          defaultMode: 'conversation',
          conversation: {
            messages: [],
            messageList: h(f.Content),
            composer: h('input', { 'data-conversation-input': true }),
          },
        }),
      ),
    '[data-conversation-input]',
  ],
  [
    'FileBrowser tree pane',
    (f, slots) =>
      wrap(
        slots,
        h(FileBrowserTreePane, {
          browser: { ...browser(), dashboardError: error(f) },
          entries: {},
          rootUri: 'viking://resources',
          treeRef: { current: null },
          locale: 'en-US',
        }),
      ),
    '[data-stable-sibling]',
  ],
  [
    'FileBrowser preview pane',
    (f, slots) =>
      wrap(
        slots,
        h(FileBrowserPreviewPane, {
          browser: { ...browser(), currentUri: target, previewError: error(f) },
          permissions: { canEdit: false, canDelete: false },
          rootUri: 'viking://resources',
          locale: 'en-US',
          text: resolveLabels(undefined, 'en-US'),
        }),
      ),
    '[data-stable-sibling]',
  ],
]) {
  test(`${name}: reset retries the actual region`, (t) =>
    verifyRetry(t, view, preserve));
}

test('FileBrowser search results retry preserves the search field and its current query', async (t) => {
  t.mock.method(console, 'error', () => {});
  const failure = fault();
  const transport = fixture(target, false, (call) =>
    call.path.endsWith('/search/find')
      ? Response.json({
          status: 'ok',
          result: { resources: [{ uri: target, abstract: 'Search result' }] },
        })
      : undefined,
  );
  const content = () =>
    wrap(
      { errorFallback: retrySlot },
      h(FileBrowserSearch, {
        open: true,
        rootUri: 'viking://resources',
        locale: 'en-US',
        triggerRef: { current: null },
        text: {
          ...resolveLabels(undefined, 'en-US'),
          searchResultCount: failure.read,
        },
        onToggleAdvanced: noop,
        onOpenResource: noop,
        onClose: noop,
      }),
      transport,
    );
  const host = await mount(content());
  try {
    await settle();
    const input = document.querySelector('[role="combobox"]');
    assert.ok(input);
    await act(() => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(input, 'audit query');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    await act(async () => new Promise((resolve) => setTimeout(resolve, 450)));
    await settle();
    const requests = transport.calls.length;
    assert.ok(
      failure.renders > 0,
      'the query must produce results before injecting a failure',
    );
    failure.broken = true;
    await host.render(content());
    assert.ok(document.querySelector('[data-host-retry]'));
    failure.broken = false;
    await host.render(content());
    const attempts = failure.renders;
    assert.ok(document.querySelector('[data-host-retry]'));
    await act(() => document.querySelector('[data-host-retry]').click());
    assert.ok(failure.renders > attempts);
    assert.equal(document.querySelector('[data-host-retry]'), null);
    assert.equal(document.querySelector('[role="combobox"]'), input);
    assert.equal(input.value, 'audit query');
    assert.equal(
      transport.calls.length,
      requests,
      'render retry does not repeat the query',
    );
  } finally {
    await host.unmount();
  }
});
