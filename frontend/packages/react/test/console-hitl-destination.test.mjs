import assert from 'node:assert/strict';
import test from 'node:test';
import { ok, recordingFetch } from './helpers/module-dom.mjs';
import {
  card,
  click,
  input,
  mountHitl,
  submit,
} from './helpers/console-hitl.mjs';

test('userId alone scopes same-origin output validation without a Provider', async () => {
  const fetch = recordingFetch(({ path, uri, init }) => {
    assert.equal(path, '/api/v1/fs/stat');
    assert.equal(uri, 'viking://resources/in-use');
    assert.equal(new Headers(init.headers).get('X-OpenViking-User'), 'user');
    return ok({ is_dir: false, uri });
  });
  const original = globalThis.fetch;
  globalThis.fetch = fetch;
  const view = await mountHitl(
    [
      card('compile_target', 'Target', {
        suggested_value: ' viking://resources/in-use/ ',
      }),
    ],
    {
      requestOptions: { userId: 'user' },
      locale: 'en-US',
    },
  );
  try {
    await view.start();
    await submit(view);
    assert.match(view.container.textContent, /already exists as a file/);
    assert.equal(fetch.calls.length, 1);
    assert.equal(view.requests.length, 1);
  } finally {
    globalThis.fetch = original;
    await view.unmount();
  }
});
