import { waitForEditor } from './helpers/preview-editor-dom.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import { mount } from './helpers/module-dom.mjs';
import { act, createElement } from 'react';

const { FilePreview } =
  await import('@openviking/react/components/file-preview');

async function setup(controlled = false) {
  const canceled = [];
  const requests = [];
  const saved = [];
  const editing = {
    loadContent: async () => 'unsaved draft',
    save: async (value) => saved.push(value),
    onCancel: (value) => canceled.push(value),
  };
  let actions;
  const tab = {
    kind: 'file',
    key: 'guide',
    source: { content: '', fileName: 'guide.md' },
    levels: ['L0', 'L1'].map((key) => ({
      key,
      label: key,
      source: { content: '', fileName: `${key}.md` },
      editing,
    })),
    ...(controlled ? { activeLevelKey: 'L0' } : {}),
    onLevelChange: (key) => requests.push(key),
    slots: {
      actions: (value) => {
        actions = value;
        return null;
      },
    },
  };
  const view = await mount(createElement(FilePreview, { tabs: [tab] }));
  await act(async () => actions.startEditing());
  await waitForEditor(view.container);
  const editor = () => view.container.querySelector('.cm-content');
  assert.equal(editor()?.textContent, 'unsaved draft');
  const clickLevel = async (key) => {
    const button = Array.from(view.container.querySelectorAll('button')).find(
      (button) => button.textContent === key,
    );
    assert.ok(button);
    await act(async () => button.click());
  };
  return {
    view,
    tab,
    canceled,
    requests,
    saved,
    editor,
    clickLevel,
    actions: () => actions,
  };
}

test('clicking the active level preserves the editor and its draft', async () => {
  const state = await setup();
  try {
    const editor = state.editor();
    await state.clickLevel('L0');
    assert.equal(state.editor(), editor);
    assert.equal(state.editor()?.textContent, 'unsaved draft');
    assert.deepEqual(state.canceled, []);
    assert.deepEqual(state.requests, []);
    await act(async () => state.actions().saveEditing());
    assert.deepEqual(state.saved, ['unsaved draft']);
  } finally {
    await state.view.unmount();
  }
});

test('an uncontrolled level change cancels once and renders the requested level', async () => {
  const state = await setup();
  try {
    await state.clickLevel('L1');
    assert.equal(state.editor(), null);
    assert.deepEqual(state.canceled, ['unsaved draft']);
    assert.deepEqual(state.requests, ['L1']);
    assert.equal(
      state.view.container.querySelector('[aria-pressed="true"]').textContent,
      'L1',
    );
  } finally {
    await state.view.unmount();
  }
});

test('controlled requests preserve drafts until the host accepts a different level', async () => {
  const state = await setup(true);
  try {
    const editor = state.editor();
    await state.clickLevel('L0');
    await state.clickLevel('L1');
    assert.deepEqual(state.requests, ['L1']);
    assert.deepEqual(state.canceled, []);
    assert.equal(state.editor(), editor);
    await state.view.render(
      createElement(FilePreview, { tabs: [{ ...state.tab }] }),
    );
    assert.equal(state.editor()?.textContent, 'unsaved draft');
    await state.view.render(
      createElement(FilePreview, {
        tabs: [{ ...state.tab, activeLevelKey: 'L1' }],
      }),
    );
    assert.equal(state.editor(), null);
    await act(async () => state.actions().startEditing());
    await waitForEditor(state.view.container);
    assert.equal(state.editor()?.textContent, 'unsaved draft');
  } finally {
    await state.view.unmount();
  }
});
