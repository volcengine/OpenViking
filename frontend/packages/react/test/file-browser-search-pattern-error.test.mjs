import assert from 'node:assert/strict';
import { test } from 'node:test';
import { act, createElement as h } from 'react';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

const hooks = process.env.OV_UI_TEST_HOOKS;
const { OpenVikingProvider } = await import(`${hooks}/provider.js`);
const { FileBrowserSearch } = await import(`${hooks}/browser-search.js`);
const { resolveLabels } = await import(`${hooks}/browser-labels.js`);
const rawMessage =
  'Invalid grep pattern: nothing to repeat at position 0 Request ID: demo-id';

async function type(input, value) {
  await act(() => {
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    ).set.call(input, value);
    input.dispatchEvent(new window.Event('input', { bubbles: true }));
  });
  await act(async () => new Promise((resolve) => setTimeout(resolve, 450)));
  await settle();
}

for (const locale of ['zh-CN', 'en-US']) {
  test(`invalid grep patterns explain the syntax and retain server details in ${locale}`, async () => {
    const fetch = recordingFetch(({ path, body }) => {
      if (path !== '/api/v1/search/grep') return ok([]);
      if (body.pattern !== '*test*') return ok({ matches: [] });
      return new Response(
        JSON.stringify({
          status: 'error',
          error: { code: 'INVALID_ARGUMENT', message: rawMessage },
        }),
        { status: 400, headers: { 'content-type': 'application/json' } },
      );
    });
    const render = (lang) =>
      h(
        OpenVikingProvider,
        { fetch },
        h(FileBrowserSearch, {
          open: true,
          rootUri: 'viking://resources',
          triggerRef: { current: null },
          advancedOpen: false,
          locale: lang,
          text: resolveLabels(undefined, lang),
          onToggleAdvanced() {},
          onOpenResource() {},
          onClose() {},
        }),
      );
    const view = await mount(render(locale));
    try {
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
                ?.textContent === 'grep',
          )
          .click(),
      );
      const input = document.querySelector('input[role="combobox"]');
      await type(input, '*test*');
      await act(
        async () => new Promise((resolve) => setTimeout(resolve, 1100)),
      );
      await settle();
      let alert = document.querySelector('[role="alert"]');
      assert.ok(alert);
      assert.match(
        alert.querySelector('p:nth-child(2)').textContent,
        locale === 'zh-CN'
          ? /正则表达式无效.*test\.\*/
          : /Invalid regular expression.*test\.\*/,
      );
      assert.equal(alert.querySelector('details').open, false);
      assert.equal(alert.querySelector('details p').textContent, rawMessage);
      assert.equal(
        document
          .querySelector('.ov-file-browser-search__summary')
          .textContent.includes('Vector retrieval'),
        false,
      );
      assert.equal(
        document
          .querySelector('.ov-file-browser-search__summary')
          .textContent.includes('向量检索方式'),
        false,
      );
      const calls = fetch.calls.filter(
        ({ path }) => path === '/api/v1/search/grep',
      ).length;
      const nextLocale = locale === 'zh-CN' ? 'en-US' : 'zh-CN';
      await view.render(render(nextLocale));
      alert = document.querySelector('[role="alert"]');
      assert.match(
        alert.querySelector('p:nth-child(2)').textContent,
        nextLocale === 'zh-CN'
          ? /正则表达式无效/
          : /Invalid regular expression/,
      );
      assert.equal(
        fetch.calls.filter(({ path }) => path === '/api/v1/search/grep').length,
        calls,
      );
      assert.equal(input.value, '*test*');
      // Python accepts inline flags that JavaScript RegExp rejects. The server owns validation.
      await type(input, '(?i)test.*');
      assert.equal(document.querySelector('[role="alert"]'), null);
      assert.equal(fetch.calls.at(-1).body.pattern, '(?i)test.*');
    } finally {
      await view.unmount();
    }
  });
}
