import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { act, createElement as h } from 'react';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import { mount } from './helpers/module-dom.mjs';

// JSDOM does not expose the browser APIs used by delayed tree reveal.
const previousCSS = globalThis.CSS;
const previousScroll = window.HTMLElement.prototype.scrollIntoView;
globalThis.CSS = {
  escape: (value) => value.replaceAll(':', '\\:').replaceAll('/', '\\/'),
};
window.HTMLElement.prototype.scrollIntoView = function () {};
after(() => {
  globalThis.CSS = previousCSS;
  window.HTMLElement.prototype.scrollIntoView = previousScroll;
});

const click = (node) =>
  act(async () => {
    node.focus();
    node.click();
  });
const submit = (form) =>
  act(async () =>
    form.dispatchEvent(
      new window.Event('submit', { bubbles: true, cancelable: true }),
    ),
  );
const button = (root, name) =>
  [...root.querySelectorAll('button')].find(
    (node) => node.textContent === name,
  );
const modal = () => document.querySelector('.ov-context-picker [role=dialog]');
const trigger = (view) =>
  view.container.querySelector('.ov-viking-bot__directory-trigger');
const uri = 'viking://resources/wiki';
const target = (operations = {}) => ({
  kind: 'target',
  initialUri: uri,
  directoryPicker: {
    rows: [
      {
        kind: 'entry',
        type: 'directory',
        id: uri,
        uri,
        name: 'wiki',
        depth: 0,
        hasChildren: false,
      },
    ],
    permission: { uri, info: { uri, isDirectory: true, canWrite: true } },
    operations,
  },
});
const reasons = [undefined, null, false, '', new Error('')];

test('target picker stays closed after disabling or submitting and preserves the answer', async () => {
  const render = (extra) =>
    h(VikingBotInteraction, { request: target(), ...extra });
  const view = await mount(render());
  try {
    for (const state of [{ disabled: true }, { submitting: true }]) {
      await click(trigger(view));
      assert.ok(modal());
      await act(async () => new Promise((resolve) => setTimeout(resolve, 350)));
      await view.render(render(state));
      assert.equal(modal(), null);
      await view.render(render());
      assert.equal(modal(), null);
      assert.equal(trigger(view).getAttribute('aria-expanded'), 'false');
      assert.match(trigger(view).textContent, /resources\/wiki/);
    }
    await click(trigger(view));
    assert.ok(modal());
    await click(button(modal(), 'Cancel'));
    assert.equal(document.activeElement, trigger(view));
  } finally {
    await view.unmount();
  }
});

test('target submission reports reasonless failures using current locale labels', async () => {
  for (const reason of reasons) {
    const render = (locale) =>
      h(VikingBotInteraction, {
        locale,
        request: target(),
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
      assert.match(trigger(view).textContent, /resources\/wiki/);
      assert.equal(trigger(view).disabled, false);
    } finally {
      await view.unmount();
    }
  }
});

test('reasonless directory creation reports a localized error, preserves input and permits retry', async () => {
  for (const reason of reasons) {
    let fail = true;
    const request = target({
      createDirectory: async (parent, name) => {
        if (fail) throw reason;
        return `${parent}/${name}`;
      },
    });
    const render = (locale) => h(VikingBotInteraction, { locale, request });
    const view = await mount(render('en-US'));
    try {
      await click(trigger(view));
      await click(button(modal(), 'New subdirectory'));
      const form = document.querySelector(
        '.ov-viking-bot__create-directory form',
      );
      const input = form.querySelector('input');
      await act(async () => {
        Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype,
          'value',
        ).set.call(input, 'notes');
        input.dispatchEvent(new window.Event('input', { bubbles: true }));
      });
      await submit(form);
      assert.ok(form.querySelector('[role=alert]')?.textContent);
      await view.render(render('zh-CN'));
      assert.equal(
        form.querySelector('[role=alert]').textContent,
        '无法创建目录，请确认目录权限后重试',
      );
      assert.equal(input.value, 'notes');
      assert.equal(input.disabled, false);
      fail = false;
      await submit(form);
      assert.equal(
        document.querySelector('.ov-viking-bot__create-directory'),
        null,
      );
      assert.match(trigger(view).textContent, /wiki\/notes/);
    } finally {
      await view.unmount();
    }
  }
});
