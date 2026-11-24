import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { FilePreview } from '@openviking/react/components/file-preview';
import { mount } from './helpers/module-dom.mjs';

const directoryTab = (uri) => ({
  kind: 'directory',
  key: uri,
  levels: [{ key: 'l0', label: 'L0', source: { path: uri, content: '' } }],
});

const fileTab = (uri) => ({
  kind: 'file',
  key: uri,
  source: { path: uri, fileName: uri.split('/').pop(), content: '' },
});

const breadcrumb = (view) =>
  view.container.querySelector('.ov-file-preview__breadcrumb');

const ancestorLabels = (view) =>
  [...view.container.querySelectorAll('.ov-file-preview__breadcrumb-link')].map(
    (node) => node.textContent,
  );

const currentLabel = (view) =>
  view.container.querySelector('.ov-file-preview__breadcrumb-current')
    ?.textContent;

// 一级目录没有祖先段，协议前缀只能由末段承担；否则面包屑只剩目录名，与更深
// 一层的目录展示不一致。
// A first-level directory has no ancestor segment, so the trailing label has to
// carry the scheme; otherwise the breadcrumb shows only the name and disagrees
// with every deeper directory.
test('a first-level directory shows the scheme on its own breadcrumb', async () => {
  for (const uri of [
    'viking://user',
    'viking://agent',
    'viking://resources',
    'viking://system',
  ]) {
    const view = await mount(h(FilePreview, { tabs: [directoryTab(uri)] }));
    try {
      assert.ok(breadcrumb(view), `missing breadcrumb for ${uri}`);
      assert.equal(currentLabel(view), uri);
      assert.deepEqual(ancestorLabels(view), []);
    } finally {
      await view.unmount();
    }
  }
});

test('deeper resources keep the scheme on the first ancestor only', async () => {
  const view = await mount(
    h(FilePreview, {
      tabs: [fileTab('viking://user/alice/settings/app.json')],
    }),
  );
  try {
    assert.deepEqual(ancestorLabels(view), [
      'viking://user',
      'alice',
      'settings',
    ]);
    assert.equal(currentLabel(view), 'app.json');
  } finally {
    await view.unmount();
  }
});

test('the scheme root and the home alias keep their existing labels', async () => {
  const root = await mount(
    h(FilePreview, { tabs: [directoryTab('viking://')] }),
  );
  try {
    assert.equal(currentLabel(root), 'viking://');
  } finally {
    await root.unmount();
  }
  const home = await mount(
    h(FilePreview, { tabs: [fileTab('viking://~/peers/agent_x/notes.md')] }),
  );
  try {
    assert.deepEqual(ancestorLabels(home), ['viking://~', 'peers', 'agent_x']);
    assert.equal(currentLabel(home), 'notes.md');
  } finally {
    await home.unmount();
  }
});
