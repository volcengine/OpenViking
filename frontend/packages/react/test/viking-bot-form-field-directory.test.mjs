import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import { mount } from './helpers/module-dom.mjs';

const click = (node) => act(async () => node.click());
const submit = (view) =>
  act(async () => {
    view.container
      .querySelector('form')
      .dispatchEvent(
        new window.Event('submit', { bubbles: true, cancelable: true }),
      );
  });
const input = (node, value) =>
  act(async () => {
    const prototype =
      node.tagName === 'TEXTAREA'
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(node, value);
    node.dispatchEvent(new window.Event('input', { bubbles: true }));
  });
const field = (node) => node.closest('.ov-form-field');
const error = (node) => field(node).querySelector('[role=alert]');
const assertInvalid = (node) => {
  assert.equal(node.getAttribute('aria-invalid'), 'true');
  assert.ok(
    node.getAttribute('aria-describedby').split(' ').includes(error(node).id),
  );
};
const assertCleared = (node) => {
  assert.equal(node.hasAttribute('aria-invalid'), false);
  assert.equal(error(node) === null, true);
  assert.equal(field(node).classList.contains('ov-form-field--error'), false);
};

test('target field clears errors after confirming a different directory', async () => {
  const uri = 'viking://resources/wiki';
  const view = await mount(
    h(VikingBotInteraction, {
      request: {
        kind: 'target',
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
        },
      },
    }),
  );
  try {
    const trigger = () =>
      view.container.querySelector('.ov-viking-bot__directory-trigger');
    await submit(view);
    assertInvalid(trigger());
    assert.equal(document.activeElement === trigger(), true);
    await click(trigger());
    const modal = document.querySelector('.ov-context-picker [role=dialog]');
    await click(modal.querySelector('[role=treeitem]'));
    await click(
      [...modal.querySelectorAll('button')].find(
        (node) => node.textContent === 'Use this directory',
      ),
    );
    assertCleared(trigger());
    assert.match(trigger().textContent, /resources\/wiki/);
    assert.equal(
      field(trigger()).querySelector('label').control === trigger(),
      true,
    );
  } finally {
    await view.unmount();
  }
});

test('editing a new directory clears its error while preserving the hint', async () => {
  const uri = 'viking://resources/wiki';
  const view = await mount(
    h(VikingBotInteraction, {
      request: {
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
          operations: {
            createDirectory: async () => {
              throw new Error('No write access');
            },
          },
        },
      },
    }),
  );
  try {
    await click(
      view.container.querySelector('.ov-viking-bot__directory-trigger'),
    );
    await click(
      [...document.querySelectorAll('.ov-context-picker button')].find(
        (node) => node.textContent === 'New subdirectory',
      ),
    );
    const form = document.querySelector(
      '.ov-viking-bot__create-directory form',
    );
    const control = form.querySelector('input');
    await input(control, 'notes');
    await submit({ container: form.parentElement });
    assertInvalid(control);
    assert.equal(error(control).textContent, 'No write access');
    await input(control, '');
    assertCleared(control);
    const hint = document.getElementById(
      control.getAttribute('aria-describedby'),
    );
    assert.ok(hint.textContent);
    await submit({ container: form.parentElement });
    assertInvalid(control);
    await input(control, 'draft');
    assertCleared(control);
    assert.equal(control.value, 'draft');
  } finally {
    await view.unmount();
  }
});
