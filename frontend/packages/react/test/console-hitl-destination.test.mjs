import assert from 'node:assert/strict';
import test from 'node:test';
import { ok, recordingFetch } from './helpers/module-dom.mjs';
import {
  card,
  click,
  input,
  mountHitl,
  settle,
  submit,
} from './helpers/console-hitl.mjs';

test('output directory suggestions and receipts display Chinese while submitting the original URI', async () => {
  const uri = 'viking://resources/%E4%B8%AD%E6%96%87%252F_wiki';
  const label = 'viking://resources/中文%252F_wiki';
  const fetch = recordingFetch(({ uri }) => ok({ uri, isDir: true }));
  const view = await mountHitl(
    [card('compile_target', 'Target', { suggested_value: uri })],
    { fetch },
  );
  try {
    await view.start();
    const option = view.container.querySelector(
      '.ov-viking-bot__interaction-option',
    );
    assert.ok(option);
    assert.ok(option.textContent.includes(label));
    assert.equal(option.querySelector('input').value, uri);
    await submit(view);
    await settle();
    assert.equal(fetch.calls[0].uri, uri);
    assert.equal(JSON.parse(view.requests[1].text).answers[0].value, uri);
    const receipt = view.props.messages.find(
      (message) => message.answerSummary,
    );
    assert.deepEqual(receipt?.answerSummary, [
      { label: 'Target', value: label },
    ]);
  } finally {
    await view.unmount();
  }
});

test('generated output directory suggestions keep literal Chinese Viking URI segments', async () => {
  const view = await mountHitl(
    [
      card('compile_name', 'Name'),
      card('compile_output_type', 'Output', {
        options: [{ value: 'wiki', label: 'Wiki' }],
      }),
      card('compile_target', 'Target'),
    ],
    { requestOptions: { userId: '中文用户' } },
  );
  try {
    await view.start();
    await input(view.container.querySelector('[name=answer]'), '研发周报');
    await submit(view);
    await click(view.container.querySelector('input[value=wiki]'));
    await submit(view);
    const values = [
      ...view.container.querySelectorAll(
        '.ov-viking-bot__interaction-option input',
      ),
    ].map((item) => item.value);
    assert.ok(values.includes('viking://resources/研发周报_wiki'));
    assert.ok(
      values.includes(
        'viking://user/%E4%B8%AD%E6%96%87%E7%94%A8%E6%88%B7/resources/研发周报_wiki',
      ),
    );
    assert.ok(
      values.every(
        (value) => !/%E7%A0%94%E5%8F%91%E5%91%A8%E6%8A%A5/i.test(value),
      ),
    );
  } finally {
    await view.unmount();
  }
});

test('generated suggestions reuse an existing encoded target with the same readable name', async () => {
  const existing =
    'viking://resources/%E7%A0%94%E5%8F%91%E5%91%A8%E6%8A%A5_wiki';
  const view = await mountHitl([
    card('compile_name', 'Name'),
    card('compile_output_type', 'Output', {
      options: [{ value: 'wiki', label: 'Wiki' }],
    }),
    card('compile_target', 'Target', { suggested_value: existing }),
  ]);
  try {
    await view.start();
    await input(view.container.querySelector('[name=answer]'), '研发周报');
    await submit(view);
    await click(view.container.querySelector('input[value=wiki]'));
    await submit(view);
    const options = [
      ...view.container.querySelectorAll('.ov-viking-bot__interaction-option'),
    ];
    const values = options.map((item) => item.querySelector('input').value);
    assert.ok(values.includes(existing));
    assert.ok(!values.includes('viking://resources/研发周报_wiki'));
    assert.equal(
      options.filter((item) =>
        item.textContent.includes('viking://resources/研发周报_wiki'),
      ).length,
      1,
    );
  } finally {
    await view.unmount();
  }
});

test('generated suggestions reuse a current-user alias for an existing encoded target', async () => {
  const existing =
    'viking://~/resources/%E7%A0%94%E5%8F%91%E5%91%A8%E6%8A%A5_wiki';
  const view = await mountHitl(
    [
      card('compile_name', 'Name'),
      card('compile_output_type', 'Output', {
        options: [{ value: 'wiki', label: 'Wiki' }],
      }),
      card('compile_target', 'Target', { suggested_value: existing }),
    ],
    { userId: 'alice' },
  );
  try {
    await view.start();
    await input(view.container.querySelector('[name=answer]'), '研发周报');
    await submit(view);
    await click(view.container.querySelector('input[value=wiki]'));
    await submit(view);
    const values = [
      ...view.container.querySelectorAll(
        '.ov-viking-bot__interaction-option input',
      ),
    ].map((item) => item.value);
    assert.ok(values.includes(existing));
    assert.ok(!values.includes('viking://user/alice/resources/研发周报_wiki'));
  } finally {
    await view.unmount();
  }
});

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
