import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement as h, createRef } from 'react';
import { act } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import { mount } from './helpers/module-dom.mjs';

function Crash() {
  throw new Error('preview rendering failed');
}

test('file body failure preserves tabs, actions, forwarded handle and recovers on a new source', async (t) => {
  t.mock.method(console, 'error', () => {});
  const ref = createRef();
  const source = {
    fileName: 'one.md',
    path: 'viking://resources/one.md',
    content: 'Loading',
  };
  const tab = {
    key: 'one',
    kind: 'file',
    title: 'First file',
    source,
    loading: true,
    slots: { loadingFallback: h(Crash) },
  };
  const second = {
    key: 'two',
    kind: 'content',
    title: 'Other file',
    content: h('p', null, 'Other content'),
  };
  const tree = (first = tab) => h(FilePreview, { ref, tabs: [first, second] });
  const host = await mount(tree());
  try {
    assert.equal(host.container.querySelectorAll('[role="tab"]').length, 2);
    assert.ok(host.container.querySelector('.ov-file-preview__header'));
    assert.ok(host.container.querySelector('.ov-error-boundary'));
    assert.match(host.container.textContent, /File preview failed/);
    await host.render(
      tree({
        ...tab,
        slots: {
          ...tab.slots,
          errorFallback: h(
            'div',
            { className: 'host-preview-error' },
            'Host error display',
          ),
        },
      }),
    );
    assert.ok(host.container.querySelector('.host-preview-error'));
    assert.equal(host.container.querySelector('.ov-error-boundary'), null);
    await act(() => ref.current.setActiveTab('two'));
    assert.match(host.container.textContent, /Other content/);
    await act(() => ref.current.setActiveTab('one'));
    await host.render(
      tree({ ...tab, loading: false, source: { ...source, content: '' } }),
    );
    assert.equal(ref.current.getActiveTab(), 'one');
    assert.equal(host.container.querySelector('.ov-error-boundary'), null);
    assert.match(host.container.textContent, /empty/i);
  } finally {
    await host.unmount();
  }
});

test('host content failure keeps tabs available; invalid entry rendering is isolated from the host', async (t) => {
  t.mock.method(console, 'error', () => {});
  const ref = createRef();
  const tabs = [
    { key: 'bad', kind: 'content', title: 'Bad', content: h(Crash) },
    { key: 'good', kind: 'content', title: 'Good', content: 'Healthy' },
  ];
  const tree = (items) =>
    h(
      'main',
      null,
      h('input', { defaultValue: 'Host draft' }),
      h(FilePreview, { ref, tabs: items }),
    );
  const host = await mount(tree(tabs));
  try {
    const input = host.container.querySelector('input');
    assert.match(
      host.container.textContent,
      /Tab content could not be displayed/,
    );
    await act(() => ref.current.setActiveTab('good'));
    assert.match(host.container.textContent, /Healthy/);
    await host.render(tree([]));
    assert.ok(host.container.querySelector('.ov-error-boundary'));
    assert.equal(host.container.querySelector('input'), input);
    assert.equal(input.value, 'Host draft');
    await host.render(tree(tabs.slice(1)));
    assert.match(host.container.textContent, /Healthy/);
  } finally {
    await host.unmount();
  }
});

test('a failed preview panel preserves an existing editor draft above the boundary', async (t) => {
  t.mock.method(console, 'error', () => {});
  const { waitForEditor, setEditor } =
    await import('./helpers/preview-editor-dom.mjs');
  const tab = {
    key: 'edit',
    kind: 'file',
    title: 'Editable',
    source: { fileName: 'draft.txt', content: '' },
    editing: { onSave: async () => {} },
  };
  const tree = (broken) =>
    h(FilePreview, {
      tabs: [{ ...tab, slots: { footer: broken ? h(Crash) : null } }],
    });
  const host = await mount(tree(false));
  try {
    const edit = [...host.container.querySelectorAll('button')].find(
      (button) => button.getAttribute('aria-label') === 'Edit',
    );
    assert.ok(edit);
    await act(() => edit.click());
    await setEditor(
      await waitForEditor(host.container),
      'unsaved editor draft',
    );
    await host.render(tree(true));
    assert.ok(host.container.querySelector('.ov-error-boundary'));
    await host.render(tree(false));
    await act(() =>
      host.container.querySelector('.ov-error-boundary button').click(),
    );
    const editor = await waitForEditor(host.container);
    assert.equal(editor.textContent, 'unsaved editor draft');
  } finally {
    await host.unmount();
  }
});
