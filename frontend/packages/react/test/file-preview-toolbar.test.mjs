import { waitForEditor, setEditor } from './helpers/preview-editor-dom.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';
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
          actions: [
            {
              key: 'custom',
              label: 'Custom command',
              icon: '⋯',
              pinned: true,
              onClick: () => {},
            },
          ],
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
    assert.ok(button(view, 'Custom command'));
    await click(view, 'Body');
    await click(view, 'Edit');
    await waitForEditor(view.container);
    assert.equal(
      view.container.querySelector('.cm-content')?.textContent,
      'full text',
    );
    assert.ok(button(view, 'Cancel')?.closest('.ov-file-preview__header'));
    assert.ok(button(view, 'Save')?.closest('.ov-file-preview__header'));
    assert.equal(button(view, 'Custom command'), undefined);
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

test('FileBrowser saves edits before offering an optional snapshot without waiting for background work', async () => {
  const rootUri = 'viking://user/alice/memories';
  const uri = `${rootUri}/guide.md`;
  let fullReads = 0;
  const requests = recordingFetch(({ path, body: request }) => {
    if (path.endsWith('/fs/ls')) return ok([{ uri, isDir: false }]);
    if (path.endsWith('/fs/stat')) return ok({ uri, isDir: false });
    if (path.endsWith('/content/read')) return ok('# Preview excerpt');
    if (path.endsWith('/content/write')) {
      assert.equal(request.uri, uri);
      assert.equal(request.mode, 'replace');
      assert.equal(request.content, '# Updated');
      assert.notEqual(request.wait, true);
      assert.equal(request.version_message, undefined);
      assert.equal(request.use_snapshot, undefined);
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
  const render = (readOnly, previewActions) =>
    createElement(
      OpenVikingProvider,
      { fetch },
      createElement(FileBrowser, { rootUri, uri, readOnly, previewActions }),
    );
  const view = await mount(render(false));
  const originalClipboard = Object.getOwnPropertyDescriptor(
    navigator,
    'clipboard',
  );
  const copied = [];
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: async (text) => copied.push(text) },
  });
  try {
    const reads = requests.countOf('/api/v1/content/read');
    await click(view, 'Edit');
    await waitForEditor(view.container);
    const toolbar = view.container.querySelector('.ov-file-preview__toolbar');
    assert.deepEqual(
      [...toolbar.querySelectorAll('button')].map(
        (node) => node.getAttribute('aria-label') ?? node.textContent.trim(),
      ),
      ['Cancel', 'Save', 'Version history', 'Copy content'],
    );
    assert.ok(button(view, 'Copy path'));
    await click(view, 'Version history');
    const history = document.querySelector('.ov-file-history');
    assert.match(history.querySelector('h2').textContent, /Version history/);
    await act(async () =>
      history.querySelector('[aria-label="Close dialog"]').click(),
    );
    assert.equal(
      view.container.querySelector('.cm-content')?.textContent,
      '# Initial',
    );
    assert.equal(fullReads, 1);
    await click(view, 'Copy content');
    const editor = view.container.querySelector('.cm-content');
    await setEditor(editor, '');
    await click(view, 'Copy content');
    await setEditor(editor, '# Updated');
    await click(view, 'Copy content');
    assert.deepEqual(copied, ['# Initial', '', '# Updated']);
    await view.render(render(false, { copyContent: false }));
    assert.equal(button(view, 'Copy content'), undefined);
    await click(view, 'Save');
    assert.equal(requests.countOf('/api/v1/content/write'), 1);
    assert.equal(requests.countOf('/api/v1/snapshot/commit'), 0);
    assert.equal(requests.countOf('/api/v1/tasks/content-write'), 0);
    assert.match(
      document.querySelector('[role="dialog"]')?.textContent ?? '',
      /Keep a snapshot\?/,
    );
    assert.equal(view.container.querySelector('.cm-content'), null);
    assert.equal(requests.countOf('/api/v1/content/read'), reads + 1);
    await view.render(render(true));
    assert.equal(button(view, 'Edit'), undefined);
  } finally {
    if (originalClipboard) {
      Object.defineProperty(navigator, 'clipboard', originalClipboard);
    } else {
      delete navigator.clipboard;
    }
    await view.unmount();
  }
});
