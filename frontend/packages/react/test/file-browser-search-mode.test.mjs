import assert from 'node:assert/strict';
import { test } from 'node:test';
import { act, createElement as h } from 'react';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

const hooks = process.env.OV_UI_TEST_HOOKS;
const { OpenVikingProvider } = await import(`${hooks}/provider.js`);
const { FileBrowserSearch } = await import(`${hooks}/browser-search.js`);
const { resolveLabels } = await import(`${hooks}/browser-labels.js`);
const text = resolveLabels(undefined, 'en-US');
const scope = 'viking://resources';
const query = 'Shenzhen';

function summary() {
  return document.querySelector('.ov-file-browser-search__summary');
}

async function selectMode(mode) {
  await act(() => summary().querySelector('button').click());
  const option = [...document.querySelectorAll('[role="option"]')].find(
    (option) =>
      option.querySelector('.ov-file-browser-search__menu-name')
        ?.textContent === mode,
  );
  assert.ok(option);
  assert.ok(option.textContent.includes(text.searchModeHints[mode]));
  await act(() => option.click());
  await settle();
  assert.ok(summary().textContent.includes(text.searchModeHints[mode]));
  assert.equal(
    summary().querySelector('.ov-file-browser-search__mode-chip').textContent,
    mode,
  );
}

for (const from of ['glob', 'grep']) {
  for (const to of ['search', 'find']) {
    test(`FileBrowser switches from empty ${from} to ${to} with the same query and directory`, async () => {
      const fetch = recordingFetch(({ path }) =>
        ok(
          path.endsWith(`/${from}`)
            ? { matches: [] }
            : { resources: [{ uri: `${scope}/Shenzhen.md` }] },
        ),
      );
      const host = await mount(
        h(
          OpenVikingProvider,
          { fetch },
          h(FileBrowserSearch, {
            open: true,
            rootUri: 'viking://',
            triggerRef: { current: null },
            advancedOpen: true,
            locale: 'en-US',
            text,
            onToggleAdvanced() {},
            onOpenResource() {},
            onClose() {},
          }),
        ),
      );
      try {
        assert.equal(
          document.querySelector(
            '.ov-file-browser-search__header button[role="combobox"]',
          ),
          null,
        );
        assert.equal(
          summary().querySelector('button').textContent.trim(),
          text.searchSwitchMode,
        );
        assert.ok(!summary().textContent.includes(text.searchResultCount(0)));
        await selectMode(from);
        const scopeInput = [...document.querySelectorAll('label')]
          .find((label) => label.textContent.trim() === scope)
          .querySelector('input');
        await act(() => scopeInput.click());
        if (from === 'grep') {
          const ignoreCase = [...document.querySelectorAll('label')]
            .find((label) => label.textContent.trim() === text.searchIgnoreCase)
            .querySelector('input');
          await act(() => ignoreCase.click());
        }
        const input = document.querySelector('input[role="combobox"]');
        await act(() => {
          Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype,
            'value',
          ).set.call(input, query);
          input.dispatchEvent(new window.Event('input', { bubbles: true }));
        });
        await act(
          async () => new Promise((resolve) => setTimeout(resolve, 450)),
        );
        await settle();
        assert.equal(
          document
            .querySelector('.ov-file-browser-search__no-results')
            .textContent.trim(),
          text.searchEmpty,
        );
        assert.equal(
          summary().firstElementChild.textContent,
          text.searchResultCount(0),
        );
        const switchButton = summary().querySelector('button');
        assert.equal(fetch.calls.at(-1).path, `/api/v1/search/${from}`);
        assert.equal(fetch.calls.at(-1).body.uri, scope);
        if (from === 'grep')
          assert.equal(fetch.calls.at(-1).body.case_insensitive, true);

        await selectMode(to);

        assert.equal(summary().querySelector('button'), switchButton);
        assert.equal(
          summary().firstElementChild.textContent,
          text.searchResultCount(1),
        );
        assert.equal(input.value, query);
        assert.equal(fetch.calls.at(-1).path, `/api/v1/search/${to}`);
        assert.deepEqual(fetch.calls.at(-1).body, {
          query,
          limit: 20,
          target_uri: [scope],
        });
        assert.match(
          document.querySelector('.ov-file-browser-search__list').textContent,
          /Shenzhen\.md/,
        );
      } finally {
        await host.unmount();
      }
    });
  }
}
