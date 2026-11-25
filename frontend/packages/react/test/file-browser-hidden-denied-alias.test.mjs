import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { mount } from './helpers/module-dom.mjs';

const { useDirectoryRows } = await import(
  `${process.env.OV_UI_TEST_HOOKS}/directory-row-view.js`
);

test('an equivalent listing handoff never reveals cached denied children', async () => {
  const root = 'viking://~/resources';
  const alias = `${root}/folder`;
  const canonical = 'viking://user/alice/resources/folder';
  const entries = [
    { uri: `${alias}/visible.md`, access: 'read' },
    { uri: `${alias}/restricted.md`, access: 'denied' },
  ];
  const page = (entries) => ({
    entries,
    ready: true,
    hasMore: false,
    loading: false,
    error: null,
  });
  const dirs = {
    [root]: page([{ uri: canonical, isDir: true }]),
    [alias]: page(entries),
  };
  let rows;
  function Probe() {
    ({ rows } = useDirectoryRows({
      rootUri: root,
      dirs,
      hideDeniedEntries: true,
      expanded: new Set([alias]),
      collapsed: new Set(),
      pendingEdits: [],
      identifyUri: (uri) => uri.replace('viking://~', 'viking://user/alice'),
      resolveHome: async () => 'viking://user/alice',
      initializing: false,
    }));
    return null;
  }
  const view = await mount(h(Probe));
  try {
    assert.deepEqual(
      rows.filter(({ kind }) => kind === 'entry').map(({ uri }) => uri),
      [canonical, entries[0].uri],
    );
    assert.equal(dirs[alias].entries, entries);
    assert.equal(dirs[alias].entries[1].access, 'denied');
  } finally {
    await view.unmount();
  }
});
