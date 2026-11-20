import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { QueryClient } from '@tanstack/react-query';
import {
  deferred,
  mount,
  ok,
  recordingFetch,
  settle,
} from './helpers/module-dom.mjs';

const hooks = process.env.OV_UI_TEST_HOOKS;
const { OpenVikingProvider } = await import(`${hooks}/provider.js`);
const { FileBrowserSearch } = await import(`${hooks}/browser-search.js`);
const { resolveLabels } = await import(`${hooks}/browser-labels.js`);
const rootUri = 'viking://resources/project';
const selector = '.ov-file-browser-search__scope-menu';
const menu = () => document.querySelector(selector);
const trigger = () =>
  document.querySelector('.ov-file-browser-search__scope-trigger');
const expand = () =>
  menu().querySelector('.ov-file-browser-search__scope-row > button');
const click = async (element) => {
  assert.ok(element);
  await act(() => element.click());
  await settle();
};
const search = () =>
  h(FileBrowserSearch, {
    open: true,
    rootUri,
    triggerRef: { current: null },
    advancedOpen: true,
    locale: 'en-US',
    text: resolveLabels(undefined, 'en-US'),
    onToggleAdvanced() {},
    onOpenResource() {},
    onClose() {},
  });

for (const failure of ['identity', 'listing']) {
  test(`directory Retry recovers a failed ${failure} without a host rerender`, async () => {
    let broken = true;
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const fetch = recordingFetch(({ path }) =>
      failure === 'listing' && broken
        ? Promise.resolve(
            Response.json(
              {
                status: 'error',
                error: { code: 'FAILED', message: 'Listing unavailable' },
              },
              { status: 500 },
            ),
          )
        : ok(
            path.endsWith('/ls')
              ? [{ uri: `${rootUri}/recovered`, isDir: true }]
              : [],
          ),
    );
    const view = await mount(
      h(
        OpenVikingProvider,
        {
          fetch,
          queryClient,
          apiKey: async () => {
            if (failure === 'identity' && broken)
              throw new Error('Identity unavailable');
            return 'fixture-credential';
          },
        },
        search(),
      ),
    );
    try {
      await click(trigger());
      await click(expand());
      const retry = [...menu().querySelectorAll('button')].find(
        (button) => button.textContent === 'Retry',
      );
      assert.ok(retry);
      const previousRequests = fetch.calls.length;
      broken = false;
      await click(retry);
      assert.ok(menu().querySelector(`label[title="${rootUri}/recovered"]`));
      assert.equal(fetch.calls.length, previousRequests + 1);
    } finally {
      await view.unmount();
      queryClient.clear();
    }
  });
}

test('replacing account ownership discards its directory draft and late branch listing', async () => {
  const pending = deferred();
  const fetch = recordingFetch(({ path, uri, init }) => {
    if (!path.endsWith('/ls')) return ok([]);
    const account = new Headers(init.headers).get('X-OpenViking-Account');
    if (uri === `${rootUri}/private-a`) return pending.promise;
    return ok([
      { uri: `${rootUri}/private-${account.toLowerCase()}`, isDir: true },
    ]);
  });
  const render = (accountId) =>
    h(OpenVikingProvider, { fetch, accountId }, search());
  const view = await mount(render('A'));
  try {
    await click(trigger());
    await click(expand());
    const selected = menu().querySelector(
      `label[title="${rootUri}/private-a"]`,
    );
    await click(selected.querySelector('input'));
    await click(
      selected
        .closest('.ov-file-browser-search__scope-row')
        .querySelector('button'),
    );
    await view.render(render('B'));
    assert.equal(menu(), null);
    await act(async () =>
      pending.resolve(
        await ok([{ uri: `${rootUri}/private-a/late-secret`, isDir: true }]),
      ),
    );
    await settle();
    await click(trigger());
    await click(expand());
    assert.ok(menu().querySelector(`label[title="${rootUri}/private-b"]`));
    assert.doesNotMatch(menu().textContent, /private-a|late-secret/);
    assert.equal(menu().querySelector('input:checked'), null);
  } finally {
    await view.unmount();
  }
});
