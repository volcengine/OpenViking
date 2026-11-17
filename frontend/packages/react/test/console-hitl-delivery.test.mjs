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
import { deferred } from './helpers/module-dom.mjs';

test('HITL closes when the bot resumes and stays closed if that reply later fails', async () => {
  const start = deferred();
  const end = deferred();
  const view = await mountHitl([card('compile_name', 'Name')], {
    async *answerEvents() {
      await start.promise;
      yield { type: 'reasoning', text: 'Checking the answer', delta: true };
      await end.promise;
      throw new Error('Stream lost after delivery');
    },
  });
  try {
    await view.start();
    await input(view.container.querySelector('[name=answer]'), 'Draft');
    await submit(view);
    assert.equal(view.requests.length, 2);
    assert.ok(view.container.querySelector('form'));

    await act(async () => start.resolve());
    await settle();
    assert.equal(view.props.interaction, undefined);
    assert.equal(view.container.querySelector('form'), null);
    assert.ok(
      view.props.streaming.parts.some((part) => part.type === 'reasoning'),
    );

    await act(async () => end.resolve());
    await settle();
    assert.equal(view.props.interaction, undefined);
    assert.equal(view.container.querySelector('form'), null);
    assert.equal(
      view.props.messages.at(-1).parts[0].text,
      'Stream lost after delivery',
    );
  } finally {
    start.resolve();
    end.resolve();
    await view.unmount();
  }
});
