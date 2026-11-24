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

test('generated output directory suggestions preserve directory names and user IDs', async () => {
  const view = await mountHitl(
    [
      card('compile_name', 'Name'),
      card('compile_output_type', 'Output', {
        options: [{ value: 'wiki', label: 'Wiki' }],
      }),
      card('compile_target', 'Target'),
    ],
    { requestOptions: { userId: 'alice@example.com' } },
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
        'viking://user/alice@example.com/resources/研发周报_wiki',
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
    { userId: 'alice@example.com' },
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
    assert.ok(
      !values.includes(
        'viking://user/alice@example.com/resources/研发周报_wiki',
      ),
    );
  } finally {
    await view.unmount();
  }
});
