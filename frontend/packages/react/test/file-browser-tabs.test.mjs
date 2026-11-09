import assert from 'node:assert/strict';
import test from 'node:test';

import {
  toDirectoryTab,
  toFileTab,
} from '../src/modules/file-browser/fs-model.ts';

test('a file tab shows the file name, not the whole URI', () => {
  const tab = toFileTab(
    { uri: 'viking://user/default/memories/profile.md' },
    'body',
    2000,
    null,
  );
  assert.equal(tab.title, 'profile.md');
  assert.equal(tab.source.fileName, 'profile.md');
  // 完整路径仍留在 source 上，标题行的面包屑要用它。
  assert.equal(tab.source.path, 'viking://user/default/memories/profile.md');
});

test('a directory tab shows the last segment, not the whole URI', () => {
  const tab = toDirectoryTab(
    'viking://user/default/memories',
    { abstract: 'a', overview: 'o' },
    { l0: 'L0', l1: 'L1' },
  );
  assert.equal(tab.title, 'memories');
  assert.equal(tab.kind, 'directory');
});
