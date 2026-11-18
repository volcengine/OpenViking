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

test('custom chat keeps manual source URIs and blocks output overlap in both directions', async () => {
  const view = await mountHitl([
    card('compile_target', 'Target', {
      suggested_value: 'viking://resources/docs/manual/wiki',
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
      ' viking://resources/docs/manual/ \r\n\r\nviking://resources/docs/manual',
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
    await enterDestination('viking://resources/docs');
    await submit(view);
    assert.match(view.container.textContent, /separate from every source/);
    await enterDestination(' viking://resources/docs/manual/ ');
    await submit(view);
    assert.match(view.container.textContent, /separate from every source/);
    assert.equal(view.requests.length, 1);

    await enterDestination(' viking://resources/docs-wiki/ ');
    await submit(view);
    const answers = JSON.parse(view.requests[1].text).answers;
    assert.deepEqual(
      answers.map(({ value }) => value),
      ['viking://resources/docs-wiki', ['viking://resources/docs/manual']],
    );
  } finally {
    await view.unmount();
  }
});

test('built-in targets leave missing or namespace-root suggestions blank and accept a nested directory', async () => {
  for (const suggested of [undefined, ' viking://resources/ ']) {
    const fetch = recordingFetch(({ uri }) => ok({ uri, isDir: true }));
    const view = await mountHitl(
      [card('compile_target', 'Target', { suggested_value: suggested })],
      { fetch, locale: 'en-US' },
    );
    try {
      await view.start();
      assert.equal(view.props.interaction.request.initialValue, undefined);
      assert.equal(
        view.container.querySelector('button[type=submit]').disabled,
        true,
      );
      await click(
        view.container.querySelector(
          '.ov-viking-bot__interaction-custom-choice input',
        ),
      );
      for (const root of [
        'viking://resources',
        'viking://~/resources',
        'viking://user/alice/resources/',
        'viking://user/alice/peers/research/resources',
        'viking://~/memories',
      ]) {
        await input(
          view.container.querySelector('input[name=custom-answer]'),
          root,
        );
        await submit(view);
        assert.match(
          view.container.textContent,
          /inside the resource or memory namespace/,
        );
        assert.equal(view.requests.length, 1);
        assert.equal(fetch.calls.length, 0);
      }
      await input(
        view.container.querySelector('input[name=custom-answer]'),
        'viking://resources/wiki',
      );
      await submit(view);
      assert.equal(fetch.calls[0].uri, 'viking://resources/wiki');
      assert.equal(
        JSON.parse(view.requests[1].text).answers[0].value,
        'viking://resources/wiki',
      );
    } finally {
      await view.unmount();
    }
  }
});
