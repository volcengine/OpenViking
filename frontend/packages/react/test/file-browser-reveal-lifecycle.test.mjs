import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, createRef } from 'react';
import {
  deferred,
  mount,
  ok,
  recordingFetch,
  settle,
} from './helpers/module-dom.mjs';

import {
  DIR,
  FILE,
  render,
  listing,
  command,
} from './helpers/file-browser-reveal.mjs';

test('listing failure rejects and retry recovers', async () => {
  let failing = true;
  const fetch = recordingFetch((call) => {
    if (call.path === '/api/v1/fs/ls' && call.uri === DIR && failing)
      return new Response(
        JSON.stringify({
          status: 'error',
          error: { code: 'FAILED', message: 'listing unavailable' },
        }),
        { status: 503 },
      );
    return listing(call);
  });
  const handle = createRef();
  const view = await mount(render(fetch, handle));
  try {
    let promise;
    await act(async () => {
      promise = handle.current.openResource(FILE).catch((error) => error);
    });
    await settle();
    assert.match((await promise).message, /listing unavailable/);
    failing = false;
    assert.equal(await command(handle, FILE), true);
  } finally {
    await view.unmount();
  }
});

for (const action of ['unmount', 'scope change']) {
  for (const target of [FILE, DIR]) {
    test(`${action} cancels a pending ${target === DIR ? 'directory expansion' : 'file navigation'}`, async () => {
      const pending = deferred();
      const fetch = recordingFetch((call) =>
        call.path === '/api/v1/fs/ls' && call.uri === DIR
          ? pending.promise
          : listing(call),
      );
      const handle = createRef();
      const view = await mount(render(fetch, handle));
      let first;
      await act(async () => {
        first = handle.current.openResource(target);
      });
      await settle();
      const signal = fetch.calls.find(
        (c) => c.path === '/api/v1/fs/ls' && c.uri === DIR,
      ).init.signal;
      if (action === 'unmount') await view.unmount();
      else await view.render(render(fetch, handle, false, 'new-account'));
      assert.equal(await first, false);
      assert.equal(signal.aborted, true);
      await act(async () => pending.resolve(await ok([{ uri: FILE }])));
      if (action !== 'unmount') await view.unmount();
    });
  }
}
