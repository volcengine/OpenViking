import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, useState } from 'react';
import {
  card,
  mountHitl,
  input,
  submit,
  click,
  settle,
} from './helpers/console-hitl.mjs';

test('configureCard distinguishes repeated card types by ID and payload', async () => {
  const calls = [];
  const view = await mountHitl(
    [
      card('compile_name', 'First', { slot: 'first' }),
      card('compile_name', 'Second', { slot: 'second' }),
    ],
    {
      hitl: {
        configureCard: (card) => ({
          fields: {
            value: {
              validate: (value) => {
                calls.push([card.id, card.payload.slot, value]);
                return card.payload.slot === 'second' && value === 'taken'
                  ? 'Duplicate'
                  : undefined;
              },
            },
          },
        }),
      },
    },
  );
  try {
    await view.start();
    await input(view.container.querySelector('[name=answer]'), 'taken');
    await submit(view);
    await input(view.container.querySelector('[name=answer]'), 'taken');
    await submit(view);
    assert.match(view.container.textContent, /Duplicate/);
    assert.deepEqual(calls, [
      ['call-1:0', 'first', 'taken'],
      ['call-1:1', 'second', 'taken'],
    ]);
    assert.equal(view.requests.length, 1);
    await input(view.container.querySelector('[name=answer]'), 'available');
    await submit(view);
    await settle();
    assert.equal(view.requests.length, 2);
  } finally {
    await view.unmount();
  }
});

test('registered custom cards submit objects, show readable summaries and block duplicate submissions', async () => {
  let finish;
  const gate = new Promise((resolve) => {
    finish = resolve;
  });
  function Widget({ context }) {
    const [value, setValue] = useState('custom');
    return h(
      'div',
      {},
      h('input', {
        value,
        disabled: context.disabled,
        onChange: (event) => {
          setValue(event.target.value);
          context.clearError();
        },
      }),
      h(
        'button',
        {
          disabled: context.disabled,
          onClick: () => context.submit({ project: value }),
        },
        'Confirm',
      ),
    );
  }
  const view = await mountHitl([card('select_project', 'Project')], {
    onAnswer: async () => gate,
    hitl: {
      cards: {
        select_project: {
          createRequest: (card) => ({
            kind: 'custom',
            question: card.title,
            render: (context) => h(Widget, { context }),
          }),
          serialize: (answer) => ({ ...answer.value, confirmed: true }),
          summarize: (value) => value.project,
        },
      },
    },
  });
  try {
    await view.start();
    await input(view.container.querySelector('input'), 'Docs');
    await click(view.container.querySelector('button'));
    await settle();
    assert.equal(view.container.querySelector('button').disabled, true);
    assert.equal(view.requests.length, 2);
    await act(async () => finish());
    await settle();
    assert.deepEqual(JSON.parse(view.requests[1].text).answers[0].value, {
      project: 'Docs',
      confirmed: true,
    });
    assert.deepEqual(
      view.props.messages.find((message) => message.answerSummary)
        .answerSummary,
      [{ label: 'Project', value: 'Docs' }],
    );
  } finally {
    await view.unmount();
  }
});

test('scope switches discard pending field validation and drafts', async () => {
  let validate;
  const gate = new Promise((resolve) => {
    validate = resolve;
  });
  const view = await mountHitl([card('compile_name', 'Name')], {
    hitl: {
      cards: { compile_name: { fields: { value: { validate: () => gate } } } },
    },
  });
  try {
    await view.start();
    await input(view.container.querySelector('[name=answer]'), 'Old account');
    await submit(view);
    await view.rerender({ scope: 'b' });
    await act(async () => validate(undefined));
    await settle();
    assert.equal(view.requests.length, 1);
    assert.equal(view.props.interaction, undefined);
    assert.deepEqual(view.props.messages, []);
  } finally {
    await view.unmount();
  }
});
