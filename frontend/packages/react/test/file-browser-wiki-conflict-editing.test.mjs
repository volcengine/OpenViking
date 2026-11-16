import assert from 'node:assert/strict';
import test from 'node:test';
import {
  act,
  button,
  click,
  fixture,
  mount,
  ok,
  rootUri,
  settle,
  target,
  tree,
} from './helpers/preview-native-dom.mjs';
import { deferred } from './helpers/module-dom.mjs';
import { waitForEditor } from './helpers/preview-editor-dom.mjs';

const file = `${target}/approvals.md`;
const otherFile = `${target}/other.md`;
const otherBody = 'This file has no pending edits.';
const body = 'The process requires two approvals.';
const pending = {
  match_text: 'two approvals',
  problem_description: 'Approval policy differs',
  reason: 'Two references disagree',
  severity: 'high',
  state: 'unfixed',
  begin_time: 100,
  fix_time: null,
  fix_plan: '',
  resolution_basis: '',
};
const fixed = {
  ...pending,
  problem_description: 'Earlier conflict is fixed',
  state: 'fixed',
  fix_time: 120,
};
const fileContent = `${body}\n<!-- CONFLICTS_CONTENT\n${JSON.stringify(pending)},\n${JSON.stringify(fixed)}\n-->`;

async function mountWikiFile(props, pendingPreview) {
  const fetch = fixture(
    file,
    false,
    ({ path, uri, query }) => {
      if (path.endsWith('/fs/ls')) {
        if (uri === rootUri)
          return ok([
            { uri: target, isDir: true, tags: ['compile_target=true'] },
          ]);
        if (uri === target)
          return ok([
            { uri: file, isDir: false, tags: ['has_conflict=true'] },
            { uri: otherFile, isDir: false },
          ]);
        return ok([]);
      }
      if (uri === otherFile) {
        if (path.endsWith('/fs/stat')) return ok({ isDir: false });
        if (path.endsWith('/content/read')) return ok(otherBody);
      }
      if (path.endsWith('/search/find'))
        return ok({ resources: [{ uri: file, level: 2 }] });
      if (path.endsWith('/content/read') && uri === file)
        return pendingPreview && query.limit !== '-1'
          ? pendingPreview.then(() => ok(fileContent))
          : ok(fileContent);
    },
    [{ to: target }],
  );
  const render = (uri) =>
    tree(fetch, uri, { defaultExpandedUri: target, ...props });
  const view = await mount(render(file));
  return {
    view,
    fetch,
    navigate: (uri) => view.render(render(uri)),
    openDashboard: () =>
      click(
        view.container.querySelector(
          `[data-file-tree-row-id="${file}"] button[aria-label="View pending Wiki conflicts"]`,
        ),
      ),
    pencil: () =>
      view.container.querySelector(
        'button[aria-label="Edit conflict: Approval policy differs"]',
      ),
    activeTab: () =>
      view.container
        .querySelector('[role="tab"][aria-selected="true"]')
        .getAttribute('data-key'),
  };
}

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
      assert.equal(editor.textContent, body);
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
    assert.equal((await waitForEditor(view.container)).textContent, body);
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
