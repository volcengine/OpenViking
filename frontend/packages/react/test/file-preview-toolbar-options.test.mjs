import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import { mount } from './helpers/module-dom.mjs';

const button = (label) =>
  [...document.querySelectorAll('button')].find(
    (node) =>
      (node.getAttribute('aria-label') ?? node.textContent.trim()) === label,
  );
const source = { content: '', fileName: 'guide.md' };
const editing = {
  loadContent: () => Promise.resolve('# Initial'),
  save: () => Promise.resolve(),
};
const tree = (toolbar, tabToolbar) =>
  h(FilePreview, {
    toolbar,
    tabs: [
      { kind: 'file', key: 'guide', source, editing, toolbar: tabToolbar },
    ],
  });
const click = async (label) => {
  assert.ok(button(label));
  await act(async () => button(label).click());
};

test('built-in editing is shown by default and component toolbar flags can hide it', async () => {
  const view = await mount(tree());
  try {
    assert.ok(button('Edit'));
    await view.render(tree({ edit: false, viewSwitch: false }));
    assert.equal(button('Edit'), undefined);
    assert.equal(view.container.querySelector('[toolbar]'), null);
    await view.render(tree({ edit: true }));
    await click('Edit');
    assert.equal(view.container.querySelector('textarea').value, '# Initial');
    await click('Cancel');
    assert.equal(view.container.querySelector('textarea'), null);
  } finally {
    await view.unmount();
  }
});

test('a host visibility switch cannot be re-enabled by a tab; tabs can further hide controls', async () => {
  const view = await mount(tree({ edit: false }, { edit: true }));
  try {
    assert.equal(button('Edit'), undefined);
    await view.render(tree({ edit: false }, { edit: undefined }));
    assert.equal(button('Edit'), undefined);
    await view.render(tree({ edit: true }, { edit: false }));
    assert.equal(button('Edit'), undefined);
    await view.render(tree({ edit: true }, {}));
    assert.ok(button('Edit'));
  } finally {
    await view.unmount();
  }
});

test('changing visibility does not reset an active editor or remove save/cancel', async () => {
  const view = await mount(tree());
  try {
    await click('Edit');
    const textarea = view.container.querySelector('textarea');
    await view.render(tree({ edit: false, viewSwitch: false }));
    assert.equal(view.container.querySelector('textarea'), textarea);
    assert.equal(textarea.value, '# Initial');
    assert.ok(button('Save'));
    assert.ok(button('Cancel'));
    await click('Save');
    assert.equal(view.container.querySelector('textarea'), null);
    assert.equal(button('Edit'), undefined);
  } finally {
    await view.unmount();
  }
});
