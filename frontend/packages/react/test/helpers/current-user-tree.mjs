import { createElement as h } from 'react';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { QueryClient } from '@tanstack/react-query';
import { ok, recordingFetch } from './module-dom.mjs';

const entry = (name) =>
  import(pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, `${name}.js`)));
export const { OpenVikingProvider, useOvClient } = await entry('provider');
const { useFsTree } = await entry('fs-tree');
const { useSearchScopePage } = await entry('scope-page');
export const { loadPage } = await entry('fs-page');
export const ROOT = 'viking://';
export const USER = 'viking://user';
export const ALICE = `${USER}/alice@example.com`;
export const BOB = `${USER}/bob`;
export const directory = (uri) => ({ uri, isDir: true });

export function backend(intercept) {
  return recordingFetch((call) => {
    const overridden = intercept?.(call);
    if (overridden !== undefined) return overridden;
    if (call.path.endsWith('/fs/ls')) {
      if (call.uri === ROOT) return ok([directory(USER)]);
      if (call.uri.replace(/\/+$/, '') === USER)
        return ok([directory(ALICE), directory(BOB)]);
      return ok([directory(`${call.uri}/resources`)]);
    }
    return ok('');
  });
}

export function harness({
  fetch = backend(),
  rootUri = USER,
  requestOptions,
  treeOptions,
  provider = {},
  nested,
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 5000 } },
  }),
} = {}) {
  let tree, picker, client;
  function Probe() {
    client = useOvClient();
    tree = useFsTree({ client, rootUri, requestOptions, ...treeOptions });
    picker = useSearchScopePage(USER, requestOptions);
    return null;
  }
  return {
    fetch,
    queryClient,
    render(override = {}) {
      const probe = nested ? h(OpenVikingProvider, nested, h(Probe)) : h(Probe);
      return h(
        OpenVikingProvider,
        {
          baseURL: 'https://ov.test',
          fetch,
          userId: 'alice@example.com',
          queryClient,
          ...provider,
          ...override,
        },
        probe,
      );
    },
    get tree() {
      return tree;
    },
    get picker() {
      return picker;
    },
    get client() {
      return client;
    },
    uris: () =>
      tree.rows.filter((row) => row.kind === 'entry').map((row) => row.uri),
    lists: () =>
      fetch.calls
        .filter((call) => call.path.endsWith('/fs/ls'))
        .map((call) => call.uri),
  };
}
