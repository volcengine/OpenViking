import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import { mount } from './helpers/module-dom.mjs';

const click = (node) => act(async () => node.click());
const submit = (view) =>
  act(async () =>
    view.container
      .querySelector('form')
      .dispatchEvent(
        new window.Event('submit', { bubbles: true, cancelable: true }),
      ),
  );
const input = (node, value) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(
      node.tagName === 'TEXTAREA'
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype,
      'value',
    ).set.call(node, value);
    node.dispatchEvent(new window.Event('input', { bubbles: true }));
  });

test('numbered options, custom answers and preselected repairs submit typed values', async () => {
  const answers = [];
  const request = {
    kind: 'choice',
    question: 'Target',
    options: [
      { value: 'wiki', label: 'Wiki', code: 'viking://resources/wiki' },
    ],
    allowCustomInput: true,
  };
  const render = (extra = {}) =>
    h(VikingBotInteraction, {
      request,
      onSubmit: (value) => answers.push(value),
      ...extra,
    });
  const view = await mount(render());
  try {
    assert.match(view.container.querySelector('code').textContent, /viking:/);
    await submit(view);
    assert.equal(answers.length, 0);
    assert.ok(view.container.querySelector('[role=alert]'));
    await click(view.container.querySelector('input[value=wiki]'));
    await submit(view);
    assert.deepEqual(answers.at(-1), { kind: 'choice', value: 'wiki' });
    await click(
      view.container.querySelector(
        '.ov-viking-bot__interaction-custom input[type=radio]',
      ),
    );
    await submit(view);
    assert.equal(answers.length, 1);
    await input(
      view.container.querySelector('[name=custom-answer]'),
      'viking://user/custom',
    );
    await submit(view);
    assert.deepEqual(answers.at(-1), {
      kind: 'choice',
      value: 'viking://user/custom',
    });
    await view.render(
      render({
        requestKey: 'repair',
        request: {
          kind: 'repair',
          initialValue: 'b',
          options: [
            { id: 'a', label: 'A' },
            { id: 'b', label: 'B' },
          ],
        },
      }),
    );
    assert.ok(view.container.querySelector('input[value=b]').checked);
    await submit(view);
    assert.deepEqual(answers.at(-1), { kind: 'repair', value: 'b' });
  } finally {
    await view.unmount();
  }
});

test('drafts survive locale changes; multiline and request identities work independently', async () => {
  const request = { kind: 'text', question: 'Name', initialValue: 'Initial' };
  const render = (props = {}) =>
    h(VikingBotInteraction, { request, requestKey: 'one', ...props });
  const view = await mount(render());
  try {
    assert.equal(view.container.querySelector('textarea'), null);
    await input(view.container.querySelector('input'), 'Edited');
    await view.render(
      render({ locale: 'zh-CN', request: { ...request, multiline: true } }),
    );
    assert.equal(view.container.querySelector('textarea').value, 'Edited');
    assert.equal(
      view.container.querySelector('button[type=submit]').textContent,
      '提交',
    );
    await view.render(render({ requestKey: 'two' }));
    assert.equal(view.container.querySelector('input').value, 'Initial');
    await view.render(
      render({
        requestKey: 'two',
        request: {
          kind: 'repair',
          initialValue: 'b',
          options: [{ id: 'b', label: 'B' }],
        },
      }),
    );
    assert.ok(view.container.querySelector('input[value=b]').checked);
  } finally {
    await view.unmount();
  }
});

test('a pending Promise prevents duplicates and rejection preserves the draft for retry', async () => {
  let reject;
  let attempts = 0;
  const pending = new Promise((_, fail) => {
    reject = fail;
  });
  const view = await mount(
    h(VikingBotInteraction, {
      request: { kind: 'text', initialValue: 'Draft' },
      onSubmit: () => {
        attempts++;
        return attempts === 1 ? pending : undefined;
      },
    }),
  );
  try {
    await submit(view);
    await submit(view);
    assert.equal(attempts, 1);
    assert.ok(view.container.querySelector('input').disabled);
    await act(async () => reject(new Error('Retry this request')));
    assert.match(
      view.container.querySelector('[role=alert]').textContent,
      /Retry this request/,
    );
    assert.equal(view.container.querySelector('input').value, 'Draft');
    assert.equal(view.container.querySelector('input').disabled, false);
    await submit(view);
    assert.equal(attempts, 2);
    assert.equal(view.container.querySelector('[role=alert]'), null);
  } finally {
    await view.unmount();
  }
});

test('IME composition cannot submit and disabled custom render callbacks cannot fire', async () => {
  const answers = [];
  const view = await mount(
    h(VikingBotInteraction, {
      request: { kind: 'text', initialValue: 'Draft' },
      onSubmit: (value) => answers.push(value),
    }),
  );
  try {
    const form = view.container.querySelector('form');
    await act(async () =>
      form.dispatchEvent(
        new window.CompositionEvent('compositionstart', { bubbles: true }),
      ),
    );
    await submit(view);
    assert.equal(answers.length, 0);
    await act(async () =>
      form.dispatchEvent(
        new window.CompositionEvent('compositionend', { bubbles: true }),
      ),
    );
    await submit(view);
    assert.equal(answers.length, 1);
    let context;
    await view.render(
      h(VikingBotInteraction, {
        disabled: true,
        request: {
          kind: 'custom',
          render: (value) => {
            context = value;
            return 'Custom';
          },
        },
        onSubmit: (value) => answers.push(value),
      }),
    );
    context.submit('blocked');
    assert.equal(answers.length, 1);
  } finally {
    await view.unmount();
  }
});
