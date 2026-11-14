import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import {
  card,
  mountHitl,
  input,
  submit,
  settle,
} from './helpers/console-hitl.mjs';

test('same-session history reload invalidates failed answer retries without blocking immediate retries', async () => {
  const view = await mountHitl([card('compile_name', 'Name')], {
    onAnswer: async () => {
      throw new Error('Offline');
    },
  });
  try {
    await view.start();
    await input(view.container.querySelector('[name=answer]'), 'Draft');
    await submit(view);
    await settle();
    assert.equal(view.requests.length, 2);
    assert.equal(view.container.querySelector('[name=answer]').value, 'Draft');
    await submit(view);
    await settle();
    assert.equal(view.requests.length, 3);
    assert.equal(view.container.querySelector('[name=answer]').value, 'Draft');
    const stale = view.props.interaction;
    assert.ok(
      view.saved.some(
        (message) =>
          message.role === 'user' &&
          message.parts[0].text.includes('request_user_input_response'),
      ),
    );
    await act(async () => view.props.sessions.onSelect('session'));
    await settle();
    assert.equal(view.props.interaction, undefined);
    assert.equal(view.container.querySelector('form'), null);
    assert.deepEqual(
      view.props.messages.find((message) => message.answerSummary)
        .answerSummary,
      [{ label: 'Name', value: 'Draft' }],
    );
    await act(() => stale.onSubmit({ kind: 'text', value: 'Stale' }));
    assert.equal(view.requests.length, 3);
  } finally {
    await view.unmount();
  }
});
