import assert from 'node:assert/strict';
import { test } from 'node:test';
import { act, createElement as h } from 'react';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

const hooks = process.env.OV_UI_TEST_HOOKS;
const { OpenVikingProvider } = await import(`${hooks}/provider.js`);
const { FileBrowserSearch } = await import(`${hooks}/browser-search.js`);
const { resolveLabels } = await import(`${hooks}/browser-labels.js`);

for (const mode of ['find', 'grep']) {
  test(`default ${mode} scope controls show readable labels while selecting the raw URI`, async () => {
    const rootUri = 'viking://resources/%E4%B8%AD%E6%96%87';
    const display = 'viking://resources/中文';
    const fetch = recordingFetch(({ path }) =>
      ok(
        path.startsWith('/api/v1/search/')
          ? { resources: [], matches: [] }
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
          text: resolveLabels({ searchScopes: 'Custom scopes' }, 'zh-CN'),
          onToggleAdvanced() {},
          onOpenResource() {},
          onClose() {},
        }),
      ),
    );
    try {
      if (mode === 'grep') {
        await act(() =>
          document
            .querySelector('.ov-file-browser-search__summary button')
            .click(),
        );
        await act(() =>
          [...document.querySelectorAll('[role="option"]')]
            .find(
              (option) =>
                option.querySelector('.ov-file-browser-search__menu-name')
                  ?.textContent === mode,
            )
            .click(),
        );
      }
      const option = document.querySelector(
        '.ov-file-browser-search__options label',
      );
      const control = option.querySelector('input');
      assert.equal(control.type, mode === 'find' ? 'checkbox' : 'radio');
      assert.equal(option.textContent.trim(), display);
      assert.equal(control.labels[0], option);
      assert.equal(
        control.getAttribute('aria-label') ??
          control.labels[0].textContent.trim(),
        display,
      );
      assert.equal(
        document.querySelector('.ov-file-browser-search__group-title')
          .textContent,
        'Custom scopes',
      );
      await act(() => control.click());
      assert.equal(control.checked, true);
      const input = document.querySelector('.ov-file-browser-search__input');
      await act(() => {
        Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype,
          'value',
        ).set.call(input, 'notes');
        input.dispatchEvent(new window.Event('input', { bubbles: true }));
      });
      await act(async () => new Promise((resolve) => setTimeout(resolve, 450)));
      await settle();
      const search = fetch.calls.find(
        ({ path }) => path === `/api/v1/search/${mode}`,
      );
      assert.ok(search);
      assert.deepEqual(
        mode === 'find' ? search.body.target_uri : search.body.uri,
        mode === 'find' ? [rootUri] : rootUri,
      );
    } finally {
      await view.unmount();
    }
  });
}
