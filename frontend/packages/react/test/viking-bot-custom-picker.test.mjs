import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import { mount, settle } from './helpers/module-dom.mjs';
const click = (node) =>
  act(async () => {
    node.focus();
    node.click();
  });
const dialog = () => document.querySelector('.ov-context-picker [role=dialog]');

test('HITL delegates the custom picker to the host and ignores stale selections', async () => {
  let context;
  const answers = [];
  const render = (disabled = false) =>
    h(VikingBotInteraction, {
      disabled,
      request: {
        kind: 'choice',
        options: [],
        allowCustomInput: {
          onOpen: (next) => {
            context = next;
          },
        },
      },
      onSubmit: (value) => answers.push(value),
    });
  const view = await mount(render());
  try {
    await click(
      view.container.querySelector(
        '.ov-viking-bot__interaction-picker-trigger',
      ),
    );
    assert.ok(context);
    assert.equal(dialog(), null);
    await act(async () => context.select('viking://resources/wiki'));
    assert.match(view.container.textContent, /viking:\/\/resources\/wiki/);
    await view.render(render(true));
    await act(async () => context.select('viking://resources/stale'));
    assert.doesNotMatch(view.container.textContent, /stale/);
    await view.render(render());
    await act(async () =>
      view.container
        .querySelector('form')
        .dispatchEvent(
          new window.Event('submit', { bubbles: true, cancelable: true }),
        ),
    );
    assert.deepEqual(answers, [
      { kind: 'choice', value: 'viking://resources/wiki' },
    ]);
  } finally {
    await view.unmount();
  }
  await act(async () => context.select('ignored-after-unmount'));
});

test('creating a custom directory notifies the host about the confirmed URI', async () => {
  const parent = 'viking://resources/wiki';
  const selected = [];
  const view = await mount(
    h(VikingBotInteraction, {
      request: {
        kind: 'choice',
        options: [],
        allowCustomInput: {
          directoryPicker: {
            rows: [
              {
                kind: 'entry',
                id: parent,
                uri: parent,
                name: 'wiki',
                type: 'directory',
                depth: 0,
                hasChildren: false,
              },
            ],
            permission: {
              uri: parent,
              info: { uri: parent, isDirectory: true, canWrite: true },
            },
            onSelectionChange: (uri) => selected.push(uri),
            operations: { createDirectory: async () => `${parent}/child` },
          },
        },
      },
    }),
  );
  try {
    await click(
      view.container.querySelector(
        '.ov-viking-bot__interaction-picker-trigger',
      ),
    );
    await click(dialog().querySelector('[role=treeitem]'));
    const create = [...dialog().querySelectorAll('button')].find(
      (node) => node.textContent === 'New subdirectory',
    );
    assert.equal(create.disabled, false);
    await click(create);
    const form = document.querySelector(
      '.ov-viking-bot__create-directory form',
    );
    const input = form.querySelector('input');
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(input, 'child');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    await act(async () =>
      form.dispatchEvent(
        new window.Event('submit', { bubbles: true, cancelable: true }),
      ),
    );
    await settle();
    assert.equal(selected.at(-1), `${parent}/child`);
    assert.match(
      view.container.textContent,
      /viking:\/\/resources\/wiki\/child/,
    );
  } finally {
    await view.unmount();
  }
});
