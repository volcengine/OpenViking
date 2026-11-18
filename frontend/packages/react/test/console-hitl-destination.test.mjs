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

test('a scoped filesystem without a Provider checks a normalized output URI', async () => {
  const fetch = recordingFetch(({ path, uri, init }) => {
    assert.equal(path, '/api/v1/fs/stat');
    assert.equal(uri, 'viking://resources/in-use');
    assert.equal(new Headers(init.headers).get('X-OpenViking-Account'), 'acct');
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
      requestOptions: { baseURL: 'https://ov.test', accountId: 'acct' },
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

test('custom chat keeps manual source URIs and blocks output overlap in both directions', async () => {
  const view = await mountHitl([
    card('compile_target', 'Target', {
      suggested_value: 'viking://resources/docs/wiki',
    }),
    card('compile_source', 'Sources'),
  ]);
  try {
    await view.start();
    const source = view.container.querySelector('textarea[name=answer]');
    assert.ok(source);
    await input(source, 'viking://resources/docs%2Fwiki');
    await submit(view);
    assert.match(view.container.textContent, /viking:\/\//);
    await input(
      source,
      ' viking://resources/docs/ \r\n\r\nviking://resources/docs',
    );
    await submit(view);
    await submit(view);
    assert.match(view.container.textContent, /separate from every source/);
    assert.equal(view.requests.length, 1);

    await click(
      view.container.querySelector(
        '.ov-viking-bot__interaction-custom-choice input',
      ),
    );
    const enterDestination = (value) =>
      input(view.container.querySelector('input[name=custom-answer]'), value);
    await enterDestination('viking://resources');
    await submit(view);
    assert.match(view.container.textContent, /separate from every source/);
    await enterDestination(' viking://resources/docs/ ');
    await submit(view);
    assert.match(view.container.textContent, /separate from every source/);
    assert.equal(view.requests.length, 1);

    await enterDestination(' viking://resources/docs-wiki/ ');
    await submit(view);
    const answers = JSON.parse(view.requests[1].text).answers;
    assert.deepEqual(
      answers.map(({ value }) => value),
      ['viking://resources/docs-wiki', ['viking://resources/docs']],
    );
  } finally {
    await view.unmount();
  }
});
