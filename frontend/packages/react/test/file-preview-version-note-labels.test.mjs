import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import { button, click, mount } from './helpers/preview-native-dom.mjs';
import { setTextarea } from './helpers/module-dom.mjs';

test('the version note uses per-field preview and active-tab label overrides', async () => {
  const saved = [];
  const view = await mount(
    h(FilePreview, {
      locale: 'en-US',
      labels: {
        versionNoteTitle: 'Preview note',
        versionNoteLabel: 'Why this changed',
        versionNotePlaceholder: '',
        versionNoteCancel: 'Keep editing',
        versionNoteConfirm: 'Save note',
      },
      tabs: [
        {
          kind: 'file',
          key: 'notes',
          source: { content: '', fileName: 'notes.md' },
          labels: {
            versionNoteTitle: 'Notes revision',
            versionNoteConfirm: 'Save notes revision',
          },
          editing: {
            versionNote: true,
            loadContent: async () => '# Before',
            save: async (...args) => saved.push(args),
          },
        },
      ],
    }),
  );
  try {
    await click(view.container.querySelector('[aria-label="Edit"]'));
    await setTextarea(view.container.querySelector('textarea'), '# After');
    await click(button('Save', view.container));
    const dialog = document.querySelector('[role="dialog"]');
    assert.equal(dialog.getAttribute('aria-label'), 'Notes revision');
    assert.equal(dialog.querySelector('h2').textContent, 'Notes revision');
    assert.equal(dialog.querySelector('label').textContent, 'Why this changed');
    assert.equal(dialog.querySelector('textarea').placeholder, '');
    assert.ok(button('Keep editing', dialog));
    await setTextarea(dialog.querySelector('textarea'), 'Updated notes');
    await click(button('Save notes revision', dialog));
    assert.deepEqual(saved, [['# After', 'Updated notes']]);
  } finally {
    await view.unmount();
  }
});
