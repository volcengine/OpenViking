import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import { mount } from './helpers/module-dom.mjs';
import { notificationContent } from './helpers/feedback-dom.mjs';

const active = (view) => view.container.querySelector('[data-active-step]');
const click = (node) => act(async () => node.click());
const button = (view, text) =>
  [...active(view).querySelectorAll('button')].find(
    (node) => node.textContent === text,
  );
const input = (node, value) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    ).set.call(node, value);
    node.dispatchEvent(new window.Event('input', { bubbles: true }));
  });
const request = {
  kind: 'steps',
  steps: [
    {
      kind: 'choice',
      question: 'Target',
      options: [{ value: 'wiki', label: 'Wiki' }],
      allowCustomInput: true,
    },
    { kind: 'text', question: 'Name', multiline: false },
  ],
};

test('steps preserve unfinished drafts and custom selection, switch locale, and submit together', async () => {
  const answers = [];
  const render = (extra = {}) =>
    h(VikingBotInteraction, {
      request,
      onSubmit: (value) => answers.push(value),
      ...extra,
    });
  const view = await mount(render());
  try {
    assert.notEqual(document.activeElement, active(view).querySelector('h3'));
    assert.equal(button(view, 'Next').disabled, true);
    assert.match(active(view).textContent, /1 \/ 2/);
    await click(
      active(view).querySelector(
        '.ov-viking-bot__interaction-custom input[type=radio]',
      ),
    );
    assert.equal(button(view, 'Next').disabled, true);
    await input(active(view).querySelector('[name=custom-answer]'), '  ');
    assert.equal(button(view, 'Next').disabled, true);
    await input(
      active(view).querySelector('[name=custom-answer]'),
      'viking://user/custom',
    );
    await click(button(view, 'Next'));
    assert.equal(answers.length, 0);
    assert.match(active(view).textContent, /2 \/ 2/);
    assert.equal(document.activeElement, active(view).querySelector('h3'));
    assert.equal(button(view, 'Submit').disabled, true);
    await input(active(view).querySelector('input'), 'Unfinished');
    await click(button(view, 'Previous'));
    assert.equal(
      active(view).querySelector('[name=custom-answer]').value,
      'viking://user/custom',
    );
    assert.ok(
      active(view).querySelector('.ov-viking-bot__interaction-custom input')
        .checked,
    );
    assert.ok(view.container.querySelector('[hidden] input').disabled);
    await view.render(render({ locale: 'zh-CN', labels: { next: '继续' } }));
    await click(button(view, '继续'));
    assert.equal(active(view).querySelector('input').value, 'Unfinished');
    await input(active(view).querySelector('input'), '  Final name  ');
    await click(button(view, '提交'));
    assert.deepEqual(answers, [
      {
        kind: 'steps',
        value: [
          { kind: 'choice', value: 'viking://user/custom' },
          { kind: 'text', value: '  Final name  ' },
        ],
      },
    ]);
    await view.render(render({ index: 10, total: 20 }));
    assert.match(active(view).textContent, /2 \/ 2/);
    await view.render(render({ requestKey: 'new' }));
    assert.match(active(view).textContent, /1 \/ 2/);
    assert.equal(button(view, 'Next').disabled, true);
  } finally {
    await view.unmount();
  }
});

test('a final rejection preserves every answer and releases navigation for retry', async () => {
  let reject;
  let attempts = 0;
  const pending = new Promise((_, fail) => {
    reject = fail;
  });
  const view = await mount(
    h(VikingBotInteraction, {
      request,
      onSubmit: () => (++attempts === 1 ? pending : undefined),
      onCancel: () => {},
    }),
  );
  try {
    await click(active(view).querySelector('input[value=wiki]'));
    await click(button(view, 'Next'));
    await input(active(view).querySelector('input'), 'Draft');
    await click(button(view, 'Submit'));
    assert.equal(button(view, 'Previous').disabled, true);
    assert.equal(button(view, 'Cancel').disabled, true);
    await act(async () =>
      active(view)
        .querySelector('form')
        .dispatchEvent(
          new window.Event('submit', { bubbles: true, cancelable: true }),
        ),
    );
    assert.equal(attempts, 1);
    await act(async () => reject(new Error('Retry this sequence')));
    assert.match(notificationContent().textContent, /Retry this sequence/);
    assert.equal(active(view).querySelector('input').value, 'Draft');
    await click(button(view, 'Previous'));
    assert.ok(active(view).querySelector('input[value=wiki]').checked);
    await click(button(view, 'Next'));
    await click(button(view, 'Submit'));
    assert.equal(attempts, 2);
    await click(button(view, 'Cancel'));
    assert.match(active(view).textContent, /1 \/ 2/);
    assert.equal(button(view, 'Next').disabled, true);
  } finally {
    await view.unmount();
  }
});

test('custom questions can advance and go back while other steps retain drafts', async () => {
  let context;
  const answers = [];
  const view = await mount(
    h(VikingBotInteraction, {
      request: {
        kind: 'steps',
        steps: [
          { kind: 'text', initialValue: 'A', multiline: false },
          {
            kind: 'custom',
            render: (value) => {
              context = value;
              return 'Custom question';
            },
          },
        ],
      },
      onSubmit: (answer) => answers.push(answer),
    }),
  );
  try {
    await click(button(view, 'Next'));
    await act(async () => context.previous());
    await input(active(view).querySelector('input'), 'B');
    await click(button(view, 'Next'));
    await act(async () => context.submit({ confirmed: true }));
    assert.deepEqual(answers, [
      {
        kind: 'steps',
        value: [
          { kind: 'text', value: 'B' },
          { kind: 'custom', value: { confirmed: true } },
        ],
      },
    ]);
  } finally {
    await view.unmount();
  }
});
