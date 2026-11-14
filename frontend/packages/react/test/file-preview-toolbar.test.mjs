import { waitForEditor, setEditor } from './helpers/preview-editor-dom.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  deferred,
  mount,
  ok,
  recordingFetch,
  settle,
  setTextarea,
} from './helpers/module-dom.mjs';
import { act, createElement } from 'react';

const { FilePreview } =
  await import('@openviking/react/components/file-preview');
const { FileBrowser } = await import('@openviking/react/modules/file-browser');
const { OpenVikingProvider } = await import('@openviking/react/client');

function button(view, label) {
  return Array.from(view.container.querySelectorAll('button')).find(
    (node) =>
      (node.getAttribute('aria-label') ?? node.textContent.trim()) === label,
  );
}

async function click(view, label) {
  const target = button(view, label);
  assert.ok(target, `Missing button: ${label}`);
  await act(async () => target.click());
  await settle();
}

test('standalone preview offers editing only on the active editable level', async () => {
  const saved = [];
  const view = await mount(
    createElement(FilePreview, {
      tabs: [
        {
          kind: 'file',
          key: 'guide',
          source: { content: '', fileName: 'guide.md' },
          levels: [
            {
              key: 'l0',
              label: 'Summary',
              source: { content: '', fileName: 'summary.md' },
            },
            {
              key: 'l2',
              label: 'Body',
              source: { content: '', fileName: 'guide.md' },
              editing: {
                loadContent: async () => 'full text',
                save: async (value) => saved.push(value),
              },
            },
          ],
        },
      ],
    }),
  );
  try {
    assert.equal(button(view, 'Edit'), undefined);
    await click(view, 'Body');
    await click(view, 'Edit');
    await waitForEditor(view.container);
    assert.equal(
      view.container.querySelector('.cm-content')?.textContent,
      'full text',
    );
    assert.ok(button(view, 'Cancel')?.closest('.ov-file-preview__header'));
    assert.ok(button(view, 'Save')?.closest('.ov-file-preview__header'));
    await click(view, 'Cancel');
    assert.deepEqual(saved, []);
    await click(view, 'Edit');
    await waitForEditor(view.container);
    await click(view, 'Save');
    assert.deepEqual(saved, ['full text']);
    assert.equal(view.container.querySelector('.cm-content'), null);
  } finally {
    await view.unmount();
  }
});

test('FileBrowser waits before snapshotting', { timeout: 5000 }, async () => {
  const uri = 'viking://resources/guide.md';
  let fullReads = 0;
  let taskReads = 0;
  const processing = deferred();
  const taskPolled = deferred();
  let fileLocked = false;
  const requests = recordingFetch(({ path, body: request }) => {
    if (path.endsWith('/fs/ls')) return ok([{ uri, isDir: false }]);
    if (path.endsWith('/fs/stat')) return ok({ uri, isDir: false });
    if (path.endsWith('/content/read')) return ok('');
    if (path.endsWith('/tasks/content-write')) {
      if (++taskReads === 1) {
        return ok({ task_id: 'content-write', status: 'running' });
      }
      taskPolled.resolve();
      return processing.promise.then(() => {
        fileLocked = false;
        return ok({ task_id: 'content-write', status: 'completed' });
      });
    }
    if (path.endsWith('/snapshot/commit')) {
      assert.deepEqual(request, { message: 'Update guide', paths: [uri] });
      if (fileLocked) {
        return Response.json(
          {
            status: 'error',
            error: {
              code: 'CONFLICT',
              message: 'A snapshot path is being processed',
              details: { uri, conflict_type: 'path_busy', retryable: true },
            },
          },
          { status: 409 },
        );
      }
      return ok({ result: 'created', commit_oid: 'commit-1' });
    }
    if (path.endsWith('/content/write')) {
      assert.equal(request.uri, uri);
      assert.equal(request.mode, 'replace');
      assert.equal(request.content, '# Updated');
      assert.equal(request.version_message, undefined);
      assert.equal(request.expected_content_sha256, undefined);
      fileLocked = true;
      return ok({ task_id: 'content-write' });
    }
    return ok([]);
  });
  const fetch = (input, init) => {
    const url = new URL(String(input), 'https://ov.test');
    if (
      url.pathname.endsWith('/content/read') &&
      url.searchParams.get('limit') === '-1'
    ) {
      fullReads++;
      return ok('# Initial');
    }
    return requests(input, init);
  };
  const render = (readOnly) =>
    createElement(
      OpenVikingProvider,
      { fetch },
      createElement(FileBrowser, { uri, readOnly }),
    );
  const view = await mount(render(false));
  try {
    const reads = requests.countOf('/api/v1/content/read');
    await click(view, 'Edit');
    await waitForEditor(view.container);
    assert.equal(
      view.container.querySelector('.cm-content')?.textContent,
      '# Initial',
    );
    assert.equal(fullReads, 1);
    await setEditor(view.container.querySelector('.cm-content'), '# Updated');
    await click(view, 'Save');
    const dialog = () =>
      document.querySelector('.ov-file-preview-version-note [role="dialog"]');
    assert.ok(dialog());
    assert.equal(requests.countOf('/api/v1/content/write'), 0);
    await setTextarea(dialog().querySelector('textarea'), ' Update guide ');
    await act(async () => dialog().querySelector('[type="submit"]').click());
    assert.equal(requests.countOf('/api/v1/content/write'), 1);
    assert.equal(requests.countOf('/api/v1/snapshot/commit'), 0);
    assert.equal(taskReads, 1);
    await act(async () => {
      await taskPolled.promise;
      assert.equal(requests.countOf('/api/v1/snapshot/commit'), 0);
      processing.resolve();
    });
    await settle();
    assert.equal(dialog(), null);
    assert.doesNotMatch(
      view.container.textContent,
      /a new version was not created/,
    );
    assert.deepEqual(
      requests.calls
        .filter(({ path }) => /\/(write|commit|content-write)$/.test(path))
        .map(({ path }) => path),
      [
        '/api/v1/content/write',
        '/api/v1/tasks/content-write',
        '/api/v1/tasks/content-write',
        '/api/v1/snapshot/commit',
      ],
    );
    assert.equal(requests.countOf('/api/v1/content/read'), reads + 1);
    await view.render(render(true));
    assert.equal(button(view, 'Edit'), undefined);
  } finally {
    await view.unmount();
  }
});
