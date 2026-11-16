import assert from 'node:assert/strict';
import test from 'node:test';
import { act, button, click, settle } from './helpers/preview-native-dom.mjs';
import { deferred } from './helpers/module-dom.mjs';
import { setEditor, waitForEditor } from './helpers/preview-editor-dom.mjs';
import {
  body,
  documentBody,
  file,
  fixedSentence,
  fixed,
  mountWikiFile,
  otherBody,
  otherFile,
  pending,
} from './helpers/wiki-conflict-editor-dom.mjs';

test('the FileBrowser editor highlights pending Wiki anchors without including metadata in the draft', async () => {
  const { view, fetch } = await mountWikiFile();
  try {
    await click(view.container.querySelector('button[aria-label="Edit"]'));
    const editor = await waitForEditor(view.container);
    const markedLines = () => [
      ...editor.querySelectorAll('.ov-file-preview__annotation-line'),
    ];
    const highlighted = () => markedLines().map((line) => line.textContent);
    assert.deepEqual(highlighted(), [body]);
    assert.equal(
      [...editor.querySelectorAll('.cm-line')]
        .map((line) => line.textContent)
        .join('\n')
        .trimEnd(),
      documentBody,
    );
    await act(async () =>
      markedLines()[0].dispatchEvent(
        new window.MouseEvent('mousemove', { bubbles: true }),
      ),
    );
    const tooltip = view.container.querySelector(
      '.ov-file-preview__annotation-tooltip',
    );
    assert.equal(
      tooltip?.querySelector('strong')?.textContent,
      pending.problem_description,
    );
    assert.equal(tooltip?.querySelector('p')?.textContent, pending.reason);
    await setEditor(editor, `New introduction\n${documentBody}`);
    assert.deepEqual(highlighted(), [body]);
    assert.equal(view.container.contains(tooltip), false);
    await setEditor(editor, `${body}\n${documentBody}`);
    assert.deepEqual(highlighted(), []);
    await setEditor(editor, 'The process requires three approvals.');
    assert.deepEqual(highlighted(), []);
    await click(button('Save', view.container));
    const write = fetch.calls.find(({ path }) =>
      path.endsWith('/content/write'),
    );
    assert.equal(write.body.content, 'The process requires three approvals.');
    assert.equal(write.body.resolve_conflicts, true);
  } finally {
    await view.unmount();
  }
});

test('the FileBrowser Wiki conflict pencil opens the matching file directly in the editor', async () => {
  const { view, fetch, openDashboard, pencil, activeTab } =
    await mountWikiFile();
  try {
    for (let attempt = 0; attempt < 2; attempt++) {
      await openDashboard();
      assert.equal(activeTab(), `dashboard:${file}`);
      const fixedRow = [...view.container.querySelectorAll('article')].find(
        (row) => row.textContent.includes(fixed.problem_description),
      );
      assert.ok(fixedRow);
      assert.equal(fixedRow.querySelector('.ov-wiki-dashboard__actions'), null);
      await click(pencil());
      const editor = await waitForEditor(view.container);
      assert.equal(activeTab(), file);
      assert.equal(editor.textContent, body + fixedSentence);
      await click(button('Cancel', view.container));
      assert.equal(view.container.querySelector('.cm-content'), null);
      assert.equal(activeTab(), file);
    }
    assert.equal(fetch.countOf('/api/v1/content/write'), 0);
  } finally {
    await view.unmount();
  }
});

test('the FileBrowser Wiki conflict pencil waits for the selected file preview', async () => {
  const preview = deferred();
  const { view, openDashboard, pencil, activeTab } = await mountWikiFile(
    undefined,
    preview.promise,
  );
  try {
    await openDashboard();
    await click(pencil());
    assert.equal(activeTab(), file);
    assert.equal(view.container.querySelector('.cm-content'), null);
    await act(async () => preview.resolve());
    assert.equal(
      (await waitForEditor(view.container)).textContent,
      body + fixedSentence,
    );
  } finally {
    await view.unmount();
  }
});

test('navigating away discards a pending Wiki conflict edit before preview loading finishes', async () => {
  const preview = deferred();
  const { view, fetch, navigate, openDashboard, pencil, activeTab } =
    await mountWikiFile(undefined, preview.promise);
  const editReads = (path) =>
    fetch.calls.filter(
      (call) =>
        call.path.endsWith('/content/read') &&
        call.uri === path &&
        call.query.limit === '-1',
    ).length;
  try {
    await openDashboard();
    const dashboardReads = editReads(file);
    await click(pencil());
    assert.equal(activeTab(), file);
    assert.equal(view.container.querySelector('.cm-content'), null);

    await navigate(otherFile);
    await settle();
    assert.equal(activeTab(), otherFile);
    assert.match(
      view.container.textContent,
      new RegExp(`${otherBody.length} characters`),
    );

    await act(async () => preview.resolve());
    await settle();
    assert.equal(activeTab(), otherFile);
    assert.equal(view.container.querySelector('.cm-content'), null);
    assert.equal(editReads(file), dashboardReads);
    assert.equal(editReads(otherFile), 0);
  } finally {
    await view.unmount();
  }
});

for (const [reason, props] of [
  ['read-only mode', { readOnly: true }],
  ['host disabling edit', { previewActions: { edit: false } }],
  ['read-only ACL', { acl: { resolvePermission: async () => 'read' } }],
]) {
  test(`the FileBrowser Wiki conflict pencil respects ${reason}`, async () => {
    const { view, openDashboard, pencil, activeTab } =
      await mountWikiFile(props);
    try {
      await openDashboard();
      assert.equal(pencil()?.getAttribute('aria-disabled'), 'true');
      await click(pencil());
      assert.equal(activeTab(), `dashboard:${file}`);
      assert.equal(view.container.querySelector('.cm-content'), null);
    } finally {
      await view.unmount();
    }
  });
}
