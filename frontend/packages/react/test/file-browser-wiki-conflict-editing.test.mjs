import assert from 'node:assert/strict';
import test from 'node:test';
import {
  act,
  button,
  click,
  ok,
  settle,
} from './helpers/preview-native-dom.mjs';
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
  wikiSaveNotice,
} from './helpers/wiki-conflict-editor-dom.mjs';

const editableBody = (editor) =>
  [...editor.querySelectorAll('.cm-line')]
    .map((line) => line.textContent)
    .join('\n');

test('the FileBrowser editor keeps Wiki metadata outside the draft and confirms a successful save', async () => {
  const writing = deferred();
  const { view, fetch } = await mountWikiFile({}, undefined, writing.promise);
  const dashboard = () =>
    view.container.querySelector('[role="tab"][data-key^="dashboard:"]');
  try {
    assert.ok(dashboard());
    await click(view.container.querySelector('button[aria-label="Edit"]'));
    const editor = await waitForEditor(view.container);
    assert.equal(dashboard(), null);
    const markedLines = () => [
      ...editor.querySelectorAll('.ov-file-preview__annotation-line'),
    ];
    const highlighted = () => markedLines().map((line) => line.textContent);
    assert.deepEqual(highlighted(), [body]);
    assert.equal(editableBody(editor).trimEnd(), documentBody);
    const suffix = editor.querySelector('.ov-file-preview__editor-suffix');
    assert.equal(suffix?.contentEditable, 'false');
    assert.equal(
      suffix?.textContent,
      `<!-- CONFLICTS_CONTENT\n${JSON.stringify(pending)},\n${JSON.stringify(fixed)}\n-->`,
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
    await setEditor(editor, `---\n${body}\n...\n${fixedSentence}`);
    assert.deepEqual(highlighted(), [body]);
    await setEditor(editor, `${body}\n${documentBody}`);
    assert.deepEqual(highlighted(), []);
    await setEditor(editor, 'The process requires three approvals.');
    assert.deepEqual(highlighted(), []);
    assert.equal(editableBody(editor), 'The process requires three approvals.');
    assert.equal(editor.lastElementChild, suffix);
    await click(button('Save', view.container));
    assert.deepEqual(wikiSaveNotice(), {
      title: 'Processing Wiki changes',
      content: 'Saving. Please wait.',
      closable: false,
    });
    await act(async () => writing.resolve(await ok({})));
    await act(() => new Promise((resolve) => setTimeout(resolve, 240)));
    await settle();
    assert.equal(wikiSaveNotice(), null);
    assert.equal(wikiSaveNotice('success')?.content, 'Saved successfully.');
    const write = fetch.calls.find(({ path }) =>
      path.endsWith('/content/write'),
    );
    assert.equal(write.body.content, 'The process requires three approvals.');
    assert.equal(write.body.resolve_conflicts, true);
    assert.ok(dashboard());
  } finally {
    await view.unmount();
  }
});

test('the FileBrowser Wiki conflict pencil opens the matching file directly in the editor', async () => {
  const { view, fetch, openDashboard, pencil, activeTab } =
    await mountWikiFile();
  const dashboard = () =>
    view.container.querySelector('[role="tab"][data-key^="dashboard:"]');
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
      assert.equal(dashboard(), null);
      assert.equal(editableBody(editor).trimEnd(), documentBody);
      await click(button('Cancel', view.container));
      assert.equal(view.container.querySelector('.cm-content'), null);
      assert.equal(activeTab(), file);
      assert.ok(dashboard());
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
      editableBody(await waitForEditor(view.container)).trimEnd(),
      documentBody,
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
