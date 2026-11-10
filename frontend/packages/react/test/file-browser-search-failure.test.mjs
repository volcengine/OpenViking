import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement } from 'react';
import {
  OpenVikingProvider,
  useFileBrowser,
} from './helpers/internal-hooks.mjs';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

test('internal search state retains a failure reason and can retry successfully', async () => {
  let available = false;
  let api;
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/search/find') {
      if (available)
        return ok({ memories: [{ uri: 'viking://user/alice/profile.md' }] });
      return new Response(
        JSON.stringify({
          status: 'error',
          error: { code: 'FORBIDDEN', message: 'search forbidden' },
        }),
        { status: 403, headers: { 'content-type': 'application/json' } },
      );
    }
    return ok(call.path === '/api/v1/fs/ls' ? [] : '');
  });
  function Probe() {
    api = useFileBrowser({
      rootUri: 'viking://',
      currentUri: null,
      controlled: false,
      onUriChange: () => {},
    });
    return null;
  }
  const view = await mount(
    createElement(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch },
      createElement(Probe),
    ),
  );
  try {
    await act(async () => api.actions.setQuery('blocked'));
    await settle();
    assert.equal(api.searching, true);
    assert.equal(api.searchLoading, false);
    assert.match(api.searchError.message, /search forbidden/);
    assert.equal(api.rows.length, 0);

    available = true;
    await act(async () => api.actions.refresh());
    await settle();
    assert.equal(api.searchError, null);
    assert.equal(api.rows[0].uri, 'viking://user/alice/profile.md');
    assert.equal(
      fetch.calls.filter((call) => call.path === '/api/v1/search/find').length,
      2,
    );
  } finally {
    await view.unmount();
  }
});
