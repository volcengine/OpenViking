import assert from 'node:assert/strict';
import { test } from 'node:test';

import { act, createElement as h } from 'react';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

const hooks = process.env.OV_UI_TEST_HOOKS;
const { OpenVikingProvider } = await import(`${hooks}/provider.js`);
const { FileBrowserSearch } = await import(`${hooks}/browser-search.js`);
const { resolveLabels } = await import(`${hooks}/browser-labels.js`);

const cases = [
  { leaf: '%E4%B8%AD%E6%96%87%E7%9B%AE%E5%BD%95', label: '中文目录' },
  {
    leaf: 'folder%2Fchild',
    label: 'folder/child',
    titleLeaf: 'folder%2Fchild',
  },
  { leaf: 'discount-50%', label: 'discount-50%' },
  { leaf: 'broken-%E4%B8', label: 'broken-%E4%B8' },
  { leaf: 'literal-%252F', label: 'literal-%2F', titleLeaf: 'literal-%252F' },
  {
    rootUri: 'viking://resources/discount-50%',
    leaf: '%E4%B8%AD%E6%96%87',
    label: '中文',
  },
];

for (const {
  leaf,
  label,
  titleLeaf = label,
  rootUri = 'viking://resources',
} of cases) {
  test(`search directory labels decode safely while preserving request URI: ${leaf}`, async () => {
    const uri = `${rootUri}/${leaf}`;
    const expectedTitle = `${rootUri}/${titleLeaf}`;
    const fetch = recordingFetch(({ path, uri: listed }) =>
      ok(
        path === '/api/v1/fs/ls' && listed === rootUri
          ? [{ uri, name: label, isDir: true }]
          : path === '/api/v1/search/find'
            ? { resources: [] }
            : [],
      ),
    );
    const view = await mount(
      h(
        OpenVikingProvider,
        { fetch },
        h(FileBrowserSearch, {
          open: true,
          rootUri,
          triggerRef: { current: null },
          advancedOpen: true,
          locale: 'zh-CN',
          text: resolveLabels(undefined, 'zh-CN'),
          onToggleAdvanced() {},
          onOpenResource() {},
          onClose() {},
        }),
      ),
    );
    try {
      const click = async (element) => {
        assert.ok(element);
        await act(() => element.click());
        await settle();
      };
      await click(
        document.querySelector('.ov-file-browser-search__scope-trigger'),
      );
      await click(
        document.querySelector('.ov-file-browser-search__scope-row > button'),
      );
      const option = document.querySelector(
        `.ov-file-browser-search__scope-menu label[title="${expectedTitle}"]`,
      );
      const observed = [
        {
          stage: 'candidate',
          title: option.title,
          label: option.querySelector('.ov-checkbox__text > span').textContent,
        },
      ];
      await click(
        option
          .closest('.ov-file-browser-search__scope-row')
          .querySelector('button'),
      );
      const current = option;
      assert.ok(
        fetch.calls.some(
          (call) => call.path === '/api/v1/fs/ls' && call.uri === uri,
        ),
      );
      await click(current.querySelector('input'));
      await click(
        document.querySelector(
          '.ov-file-browser-search__scope-actions button:last-child',
        ),
      );
      const selected = document.querySelector(
        '.ov-file-browser-search__scope-picker button[aria-pressed="true"]',
      );
      observed.push({
        stage: 'selected',
        title: selected.title,
        label: selected.querySelector('span').textContent,
      });
      const input = document.querySelector('.ov-file-browser-search__input');
      await act(() => {
        Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype,
          'value',
        ).set.call(input, 'notes');
        input.dispatchEvent(new window.Event('input', { bubbles: true }));
      });
      await act(() => new Promise((resolve) => setTimeout(resolve, 450)));
      await settle();
      const search = fetch.calls
        .filter((call) => call.path === '/api/v1/search/find')
        .at(-1);
      assert.deepEqual(search.body.target_uri, [uri]);
      for (const item of observed) {
        assert.equal(item.title, expectedTitle, `${item.stage} hover`);
        assert.equal(
          item.label,
          item.stage === 'selected' ? expectedTitle : label,
          `${item.stage} label`,
        );
      }
    } finally {
      await view.unmount();
    }
  });
}

test('the initial encoded root scope label is readable without changing its selected URI', async () => {
  const rootUri = 'viking://resources/%E4%B8%AD%E6%96%87';
  const display = 'viking://resources/中文';
  const fetch = recordingFetch(({ path }) =>
    ok(path === '/api/v1/search/find' ? { resources: [] } : []),
  );
  const view = await mount(
    h(
      OpenVikingProvider,
      { fetch },
      h(FileBrowserSearch, {
        open: true,
        rootUri,
        triggerRef: { current: null },
        advancedOpen: true,
        locale: 'zh-CN',
        text: resolveLabels(undefined, 'zh-CN'),
        onToggleAdvanced() {},
        onOpenResource() {},
        onClose() {},
      }),
    ),
  );
  try {
    await act(() =>
      document.querySelector('.ov-file-browser-search__scope-trigger').click(),
    );
    const root = document.querySelector(
      '.ov-file-browser-search__scope-menu label[title]',
    );
    assert.equal(root.title, display);
    assert.equal(
      root.querySelector('.ov-checkbox__text > span').textContent,
      display,
    );
    await act(() =>
      root
        .closest('.ov-file-browser-search__scope-row')
        .querySelector('button')
        .click(),
    );
    await settle();
    assert.equal(fetch.calls.at(-1).uri, rootUri);
    await act(() => root.querySelector('input').click());
    await act(() =>
      document
        .querySelector(
          '.ov-file-browser-search__scope-actions button:last-child',
        )
        .click(),
    );
    const input = document.querySelector('.ov-file-browser-search__input');
    await act(() => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(input, 'notes');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    await act(() => new Promise((resolve) => setTimeout(resolve, 450)));
    await settle();
    assert.deepEqual(fetch.calls.at(-1).body.target_uri, [rootUri]);
  } finally {
    await view.unmount();
  }
});
