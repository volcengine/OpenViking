import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import { mount, settle } from './helpers/module-dom.mjs';

const folder = (id, extra = {}) => ({
  kind: 'entry',
  type: 'directory',
  id,
  name: id,
  uri: `viking://resources/${id}`,
  depth: 0,
  hasChildren: false,
  ...extra,
});
const rows = [
  folder('wiki'),
  folder('archive'),
  folder('locked', { disabled: true }),
  { ...folder('readme'), type: 'file' },
];
const click = (node) =>
  act(async () => {
    node.focus();
    node.click();
  });
const modal = () => document.querySelector('.ov-context-picker [role=dialog]');
const confirm = () =>
  [...modal().querySelectorAll('button')].find(
    (node) => node.textContent === 'Use this directory',
  );
const cancel = () =>
  [...modal().querySelectorAll('button')].find(
    (node) => node.textContent === 'Cancel',
  );
const trigger = (view) =>
  view.container.querySelector('.ov-viking-bot__interaction-picker-trigger');
const treeItem = (name) =>
  [...modal().querySelectorAll('[role=treeitem]')].find((node) =>
    node.textContent.includes(name),
  );
const request = {
  kind: 'choice',
  options: [{ value: 'recommended', label: 'Recommended' }],
  allowCustomInput: { directoryPicker: { rows } },
};
const submit = (view) =>
  act(async () =>
    view.container
      .querySelector('form')
      .dispatchEvent(
        new window.Event('submit', { bubbles: true, cancelable: true }),
      ),
  );

test('directory selection is staged until confirmation; cancellation preserves the previous answer', async () => {
  const answers = [];
  const view = await mount(
    h(VikingBotInteraction, {
      request,
      onSubmit: (answer) => answers.push(answer),
    }),
  );
  try {
    await click(view.container.querySelector('input[value=recommended]'));
    await click(trigger(view));
    assert.equal(modal().getAttribute('aria-label'), 'Choose a save directory');
    assert.ok(confirm().disabled);
    assert.equal(treeItem('readme') === undefined, true);
    await click(treeItem('locked'));
    assert.ok(confirm().disabled);
    await click(treeItem('wiki'));
    assert.equal(confirm().disabled, false);
    await click(cancel());
    await settle();
    assert.equal(modal() === null, true);
    assert.equal(
      document.activeElement === trigger(view),
      true,
      'focus returns to the picker trigger',
    );
    await submit(view);
    assert.deepEqual(answers.at(-1), { kind: 'choice', value: 'recommended' });
    await click(trigger(view));
    await click(treeItem('wiki'));
    await click(confirm());
    await settle();
    assert.equal(modal() === null, true);
    assert.match(trigger(view).textContent, /viking:\/\/resources\/wiki/);
    assert.equal(
      document.activeElement === trigger(view),
      true,
      'focus returns to the picker trigger',
    );
    await submit(view);
    assert.deepEqual(answers.at(-1), {
      kind: 'choice',
      value: 'viking://resources/wiki',
    });
    await click(trigger(view));
    assert.equal(treeItem('wiki').getAttribute('aria-selected'), 'true');
    await click(treeItem('archive'));
    await act(async () =>
      document.activeElement.dispatchEvent(
        new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
      ),
    );
    await settle();
    assert.equal(modal() === null, true);
    await submit(view);
    assert.deepEqual(answers.at(-1), {
      kind: 'choice',
      value: 'viking://resources/wiki',
    });
  } finally {
    await view.unmount();
  }
});

test('disabled interactions close the picker and cannot reopen it; host loading and error views stay inside the dialog', async () => {
  const render = (props = {}) => h(VikingBotInteraction, { request, ...props });
  const view = await mount(render());
  try {
    await click(trigger(view));
    assert.ok(modal());
    await view.render(render({ disabled: true }));
    assert.equal(modal() === null, true);
    assert.ok(trigger(view).disabled);
    await click(trigger(view));
    assert.equal(modal() === null, true);
    await view.render(render());
    assert.equal(modal() === null, true);
    await view.render(
      render({
        request: {
          ...request,
          allowCustomInput: {
            directoryPicker: {
              rows: [],
              isReady: false,
              loadingFallback: 'Loading directories',
            },
          },
        },
      }),
    );
    await click(trigger(view));
    assert.match(modal().textContent, /Loading directories/);
    assert.ok(confirm().disabled);
    await view.render(
      render({
        request: {
          ...request,
          allowCustomInput: {
            directoryPicker: {
              rows: [{ kind: 'error', id: 'error', depth: 0 }],
              errorFallback: 'Directory load failed',
            },
          },
        },
      }),
    );
    assert.match(modal().textContent, /Directory load failed/);
    assert.ok(confirm().disabled);
  } finally {
    await view.unmount();
  }
});
