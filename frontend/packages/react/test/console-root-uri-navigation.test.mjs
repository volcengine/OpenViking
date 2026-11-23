import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, useState } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { Console } from '@openviking/react/modules/console';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { scrolled } from './helpers/file-browser-reveal.mjs';
import {
  deferred,
  mount,
  ok,
  recordingFetch,
  setTextarea,
  settle,
} from './helpers/module-dom.mjs';

const root = 'viking://';
const users = `${root}user`;
const home = `${users}/alice`;
const resources = `${home}/resources`;
const importedFolder = `${resources}/README_CN_3`;

test('a completed Console add-resource link reveals its directory in a controlled FileBrowser', async () => {
  const completion = deferred();
  const entries = new Map([
    [root, [{ uri: users, isDir: true }]],
    [users, [{ uri: home, isDir: true }]],
    [home, [{ uri: resources, isDir: true }]],
    [resources, [{ uri: importedFolder, isDir: true }]],
  ]);
  const fetch = recordingFetch(({ path, uri }) => {
    switch (path) {
      case '/api/v1/resources':
        return ok({
          task_id: 'import-task',
          status: 'success',
          root_uri: importedFolder,
        });
      case '/api/v1/tasks/import-task':
        return completion.promise;
      case '/api/v1/fs/ls':
        return ok(entries.get(uri) ?? []);
      case '/api/v1/fs/stat':
        return ok({ isDir: true });
      default:
        return ok('');
    }
  });
  const provider = {
    fetch,
    baseURL: 'https://ov.test',
    accountId: 'workspace',
    userId: 'alice',
  };

  function Host() {
    const [uri, setUri] = useState('');
    return h(
      'main',
      null,
      h(
        OpenVikingProvider,
        provider,
        h(FileBrowser, {
          uri,
          onUriChange: (nextUri) => setUri(nextUri || ''),
          defaultExpandedUri: home,
        }),
      ),
      h(
        OpenVikingProvider,
        provider,
        h(Console, { modes: ['command'], onOpenUri: setUri }),
      ),
    );
  }

  scrolled.length = 0;
  const view = await mount(h(Host));
  try {
    const query = (selector) => view.container.querySelector(selector);
    const row = (uri) => query(`[data-file-tree-row-id="${uri}"]`);
    assert.equal(row(resources)?.getAttribute('aria-expanded'), 'false');
    assert.equal(row(importedFolder), null);

    const input = query('textarea');
    await setTextarea(input, 'ov add-resource https://example.com/readme.md');
    await act(() =>
      input.dispatchEvent(
        new window.KeyboardEvent('keydown', {
          key: 'Enter',
          ctrlKey: true,
          bubbles: true,
          cancelable: true,
        }),
      ),
    );
    await settle();
    assert.equal(fetch.countOf('/api/v1/tasks/import-task'), 1);
    await act(() =>
      completion.resolve(
        ok({ task_id: 'import-task', status: 'completed', stage: 'completed' }),
      ),
    );
    await settle();

    const link = query(
      `.ov-command-console__record[data-status="success"] a[href="${importedFolder}"]`,
    );
    assert.ok(link, 'the completed command exposes its Root uri');
    await act(() => link.click());
    await settle();

    assert.ok(
      fetch.calls.some(
        ({ path, uri }) =>
          path === '/api/v1/content/abstract' && uri === importedFolder,
      ),
      'the imported directory preview loads',
    );
    assert.equal(row(resources)?.getAttribute('aria-expanded'), 'true');
    assert.equal(row(importedFolder)?.getAttribute('aria-selected'), 'true');
    assert.deepEqual(scrolled, [importedFolder]);
  } finally {
    await view.unmount();
  }
});
