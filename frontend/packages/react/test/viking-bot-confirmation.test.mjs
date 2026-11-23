import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { VikingBotConfirmation } from '@openviking/react/components/viking-bot/interaction';
import { VikingBotConfirmation as BundledConfirmation } from '@openviking/react/components/viking-bot';
import { mount, settle } from './helpers/module-dom.mjs';
import { notificationContent } from './helpers/feedback-dom.mjs';

const click = (node) => act(async () => node.click());
const submit = (view) =>
  act(async () => {
    view.container
      .querySelector('form')
      .dispatchEvent(
        new window.Event('submit', { bubbles: true, cancelable: true }),
      );
  });
const rows = [
  {
    label: 'Source',
    value:
      'viking://resources/research/meeting-notes\nviking://resources/research/references',
  },
  { label: 'Skill', value: 'viking://skills/llm-wiki' },
  { label: 'Output', value: 'viking://resources/research-wiki' },
  { label: 'Update', value: 'Daily 10:30 (Asia/Shanghai)' },
  { label: 'Additional instructions', value: 'Keep source citations' },
];

test('public confirmation preserves every row and reports editing without submitting', async () => {
  assert.equal(VikingBotConfirmation, BundledConfirmation);
  let edits = 0;
  let confirmations = 0;
  const render = (extra = {}) =>
    h(VikingBotConfirmation, {
      rows,
      onEdit: () => edits++,
      onConfirm: () => confirmations++,
      ...extra,
    });
  const view = await mount(render());
  try {
    assert.equal(view.container.querySelectorAll('dt').length, rows.length);
    for (const row of rows)
      assert.ok(view.container.textContent.includes(row.value));
    await click(view.container.querySelector('button[type=button]'));
    assert.equal(edits, 1);
    assert.equal(confirmations, 0);
    await view.render(
      render({ locale: 'zh-CN', labels: { edit: '检查配置' } }),
    );
    assert.match(view.container.textContent, /检查配置/);
    assert.match(view.container.textContent, /确认并提交/);
    await submit(view);
    assert.equal(confirmations, 1);
  } finally {
    await view.unmount();
  }
});

test('async confirmation locks both actions, retains rows after failure and permits retry', async () => {
  let finish;
  let attempts = 0;
  let edits = 0;
  const view = await mount(
    h(VikingBotConfirmation, {
      rows,
      onEdit: () => edits++,
      onConfirm: () => {
        attempts++;
        return new Promise((resolve, reject) => {
          finish = (error) => (error ? reject(error) : resolve());
        });
      },
    }),
  );
  try {
    await submit(view);
    assert.equal(attempts, 1);
    assert.ok(
      [...view.container.querySelectorAll('button')].every(
        (button) => button.disabled,
      ),
    );
    await submit(view);
    await click(view.container.querySelector('button[type=button]'));
    assert.equal(attempts, 1);
    assert.equal(edits, 0);
    await act(async () => finish(new Error('Offline')));
    await settle();
    assert.match(notificationContent().textContent, /Offline/);
    assert.equal(view.container.querySelectorAll('dd').length, rows.length);
    await submit(view);
    assert.equal(attempts, 2);
    await act(async () => finish());
    assert.ok(
      [...view.container.querySelectorAll('button')].every(
        (button) => !button.disabled,
      ),
    );
    await click(view.container.querySelector('button[type=button]'));
    assert.equal(edits, 1);
  } finally {
    await view.unmount();
  }
});

test('confirmation updates the fallback after a rejection without a reason', async () => {
  const render = (extra = {}) =>
    h(VikingBotConfirmation, {
      rows,
      onConfirm: () => Promise.reject(),
      ...extra,
    });
  const view = await mount(render());
  try {
    await submit(view);
    await settle();
    assert.equal(
      notificationContent().textContent,
      'Submission failed. Please try again.',
    );
    await view.render(render({ locale: 'zh-CN' }));
    assert.equal(notificationContent().textContent, '提交失败，请重试');
    await view.render(render({ labels: { submitFailed: 'Try again later' } }));
    assert.equal(notificationContent().textContent, 'Try again later');
  } finally {
    await view.unmount();
  }
});

test('pending confirmation uses the current fallback and preserves supplied errors', async () => {
  let reject;
  let attempts = 0;
  const onConfirm = () => {
    attempts++;
    return new Promise((_, fail) => {
      reject = fail;
    });
  };
  const render = (locale) =>
    h(VikingBotConfirmation, { rows, onConfirm, locale });
  const view = await mount(render('en-US'));
  try {
    await submit(view);
    await view.render(render('zh-CN'));
    await submit(view);
    assert.equal(attempts, 1);
    await act(async () => reject());
    await settle();
    assert.equal(notificationContent().textContent, '提交失败，请重试');

    await submit(view);
    assert.equal(attempts, 2);
    await act(async () => reject(new Error('Offline')));
    await settle();
    await view.render(render('en-US'));
    assert.equal(notificationContent().textContent, 'Offline');
  } finally {
    await view.unmount();
  }
});
