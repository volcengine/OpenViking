import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import { mount, setTextarea } from './helpers/module-dom.mjs';

const submit = (form) =>
  act(async () =>
    form.dispatchEvent(
      new window.Event('submit', { bubbles: true, cancelable: true }),
    ),
  );
const reasons = [undefined, null, false, '', new Error('')];

test('legacy text requests keep multiline display and submission; single-line inputs are explicit', async () => {
  const answers = [];
  const request = { kind: 'text', initialValue: 'line one\nline two' };
  const render = (extra = {}) =>
    h(VikingBotInteraction, {
      request,
      onSubmit: (answer) => answers.push(answer),
      ...extra,
    });
  const view = await mount(render());
  try {
    assert.equal(
      view.container.querySelector('textarea').value,
      request.initialValue,
    );
    await submit(view.container.querySelector('form'));
    assert.equal(answers.at(-1).value, request.initialValue);
    await setTextarea(
      view.container.querySelector('textarea'),
      'edited\nanswer',
    );
    await view.render(render({ locale: 'zh-CN' }));
    await submit(view.container.querySelector('form'));
    assert.equal(
      answers.at(-1).value,
      view.container.querySelector('textarea').value,
    );
    await view.render(
      render({
        requestKey: 'single',
        request: { kind: 'text', multiline: false, initialValue: 'single' },
      }),
    );
    assert.equal(view.container.querySelector('textarea'), null);
    assert.equal(view.container.querySelector('input').value, 'single');
  } finally {
    await view.unmount();
  }
});

test('reasonless form rejections use current locale labels without losing the draft', async () => {
  for (const reason of reasons) {
    const render = (locale) =>
      h(VikingBotInteraction, {
        locale,
        request: { kind: 'text', initialValue: 'draft' },
        onSubmit: () => Promise.reject(reason),
      });
    const view = await mount(render('en-US'));
    try {
      await submit(view.container.querySelector('form'));
      assert.equal(
        view.container.querySelector('[role=alert]').textContent,
        'Submission failed. Please try again.',
      );
      await view.render(render('zh-CN'));
      assert.equal(
        view.container.querySelector('[role=alert]').textContent,
        '提交失败，请重试',
      );
      assert.equal(view.container.querySelector('textarea').value, 'draft');
      assert.equal(
        view.container.querySelector('button[type=submit]').disabled,
        false,
      );
    } finally {
      await view.unmount();
    }
  }
});

test('host error content remains unchanged when locale changes', async () => {
  for (const reason of ['host failure', new Error('host failure')]) {
    const render = (locale) =>
      h(VikingBotInteraction, {
        locale,
        request: { kind: 'text', initialValue: 'draft' },
        onSubmit: () => Promise.reject(reason),
      });
    const view = await mount(render('en-US'));
    try {
      await submit(view.container.querySelector('form'));
      await view.render(render('zh-CN'));
      assert.equal(
        view.container.querySelector('[role=alert]').textContent,
        'host failure',
      );
    } finally {
      await view.unmount();
    }
  }
});
