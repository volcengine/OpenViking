import assert from 'node:assert/strict';
import test from 'node:test';
import {
  act,
  createElement as h,
  startTransition,
  StrictMode,
  Suspense,
  useState,
} from 'react';
import { QueryClient } from '@tanstack/react-query';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

const root = 'viking://resources';
const folder = `${root}/folder`;
const leaf = `${folder}/result.md`;
const row = (view, uri) =>
  view.container.querySelector(`[data-file-tree-row-id="${uri}"]`);

for (const strict of [false, true]) {
  test(`an uncommitted account render preserves the visible expansion toggle (StrictMode ${strict})`, async () => {
    let changeAccount;
    let candidateRendered = false;
    const pending = new Promise(() => {});
    function Hold({ account }) {
      if (account === 'B') {
        candidateRendered = true;
        throw pending;
      }
      return null;
    }
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const fetch = recordingFetch(({ path, uri }) =>
      path === '/api/v1/fs/ls'
        ? ok(uri === root ? [{ uri: folder, isDir: true }] : [{ uri: leaf }])
        : ok(''),
    );
    const reports = [];
    function Host() {
      const [account, setAccount] = useState('A');
      changeAccount = () => startTransition(() => setAccount('B'));
      return h(
        Suspense,
        { fallback: h('p', {}, 'Account transition pending') },
        h(FileBrowser, {
          rootUri: root,
          dashboardKinds: [],
          defaultExpandedUris: [],
          requestOptions: { accountId: account },
          onExpandedUrisChange: (uris) => reports.push([...uris]),
        }),
        h(Hold, { account }),
      );
    }
    const app = h(
      OpenVikingProvider,
      { fetch, baseURL: 'https://ov.test', queryClient },
      h(Host),
    );
    const view = await mount(strict ? h(StrictMode, {}, app) : app);
    const toggle = async () => {
      await act(() =>
        row(view, folder).querySelector('[data-role="toggle"]').click(),
      );
      await settle();
    };
    try {
      await toggle();
      assert.equal(row(view, folder)?.getAttribute('aria-expanded'), 'true');
      assert.ok(row(view, leaf));

      await act(() => changeAccount());
      await settle();
      assert.equal(candidateRendered, true);
      assert.ok(
        !view.container.textContent.includes('Account transition pending'),
      );
      assert.equal(row(view, folder)?.getAttribute('aria-expanded'), 'true');

      await toggle();
      assert.equal(row(view, folder)?.getAttribute('aria-expanded'), 'false');
      assert.equal(row(view, leaf), null);
      assert.deepEqual(reports.at(-1), []);
    } finally {
      await view.unmount();
    }
  });
}
