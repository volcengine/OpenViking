import assert from 'node:assert/strict';
import test from 'node:test';
import { flattenResult } from '../src/modules/file-browser/search-hit-order.ts';

import { normalizeFindResult } from '../src/modules/file-browser/search-model.ts';
import {
  flattenTree,
  nameFromUri,
  toDirectoryTab,
  toEntryRow,
  toFileTab,
} from '../src/modules/file-browser/fs-model.ts';

test('a name comes from the last URI segment', () => {
  assert.equal(
    nameFromUri('viking://user/default/memories/profile.md'),
    'profile.md',
  );
  assert.equal(nameFromUri('viking://user/default/memories/'), 'memories');
  assert.equal(nameFromUri('viking://user'), 'user');
  assert.equal(nameFromUri('viking://'), 'viking:');
});

test('a directory row is expandable and carries no expanded flag on files', () => {
  const dir = toEntryRow(
    { uri: 'viking://a/b', isDir: true, abstract: '  ' },
    2,
    true,
  );
  assert.equal(dir.type, 'directory');
  assert.equal(dir.expanded, true);
  assert.equal(dir.depth, 2);
  assert.equal(dir.id, 'viking://a/b');
  // 空白摘要不该变成一行空白说明。
  assert.equal(dir.description, undefined);

  const file = toEntryRow({ uri: 'viking://a/b.md' }, 0, false);
  assert.equal(file.type, 'file');
  assert.equal(file.expanded, undefined);
});

test('a search result row keeps a unique id even for a repeated URI', () => {
  const first = toEntryRow({ uri: 'viking://a.md' }, 0, false, 0);
  const second = toEntryRow({ uri: 'viking://a.md' }, 0, false, 1);
  assert.notEqual(first.id, second.id);
  // 选中的是 URI，不是行 id，因此两者仍指向同一个资源。
  assert.equal(first.uri, second.uri);
});

test('a file tab reports truncation only when the line cap was filled', () => {
  const short = toFileTab({ uri: 'viking://a.md' }, 'one\ntwo', 2000);
  assert.equal(short.source.truncated, false);
  assert.equal(short.source.fileName, 'a.md');
  assert.equal(short.source.path, 'viking://a.md');

  const full = toFileTab({ uri: 'viking://a.md' }, 'x\n'.repeat(2000), 2000);
  assert.equal(full.source.truncated, true);
});

test('a file tab has a stable key from its URI', () => {
  const tab = toFileTab({ uri: 'viking://a/b.md' }, '', 2000);
  assert.equal(tab.key, 'viking://a/b.md');
  assert.equal(tab.kind, 'file');
});

test('flattening keeps only expanded branches and appends paging rows', () => {
  const dirs = {
    'viking://root': {
      entries: [
        { uri: 'viking://root/dir', isDir: true },
        { uri: 'viking://root/file.md' },
      ],
      hasMore: true,
      loading: false,
      error: null,
    },
    'viking://root/dir': {
      entries: [{ uri: 'viking://root/dir/inner.md' }],
      hasMore: false,
      loading: false,
      error: null,
    },
  };

  const collapsed = flattenTree(
    'viking://root',
    0,
    dirs,
    new Set(['viking://root']),
  );
  assert.deepEqual(
    collapsed.map((row) => row.kind),
    ['entry', 'entry', 'load-more'],
  );

  const opened = flattenTree(
    'viking://root',
    0,
    dirs,
    new Set(['viking://root', 'viking://root/dir']),
  );
  assert.deepEqual(
    opened.map((row) => [row.kind, row.depth]),
    [
      ['entry', 0],
      ['entry', 1],
      ['entry', 0],
      ['load-more', 0],
    ],
  );
});

test('a failed directory shows an error row instead of a paging row', () => {
  const dirs = {
    'viking://root': {
      entries: [{ uri: 'viking://root/dir', isDir: true }],
      hasMore: true,
      loading: false,
      error: new Error('boom'),
    },
  };
  const rows = flattenTree(
    'viking://root',
    0,
    dirs,
    new Set(['viking://root']),
  );
  assert.equal(rows.at(-1).kind, 'error');
});

test('search results flatten into one list deduplicated by URI', () => {
  const merged = flattenResult(
    normalizeFindResult({
      memories: [
        { uri: 'viking://a.md', abstract: ' A ' },
        { uri: 'viking://b.md' },
      ],
      resources: [{ uri: 'viking://a.md' }],
      skills: [{ uri: 'viking://c.md' }],
    }),
  );

  assert.deepEqual(
    merged.map((hit) => hit.uri),
    ['viking://a.md', 'viking://b.md', 'viking://c.md'],
  );
  assert.equal(merged[0].abstract, 'A');
});

test('a missing search result becomes an empty list', () => {
  assert.deepEqual(flattenResult(null), []);
  assert.deepEqual(flattenResult(normalizeFindResult({})), []);
});

test('an expanded directory still loading gets a loading row, not an empty one', () => {
  // 展开后首屏还没回来：entries 此刻是空的，但这不是「目录为空」。
  // Expanded with the first page in flight: entries is empty, but that is not
  // "the directory is empty".
  const dirs = {
    'viking://root': {
      entries: [{ uri: 'viking://root/slow', isDir: true }],
      hasMore: false,
      loading: false,
      error: null,
    },
    'viking://root/slow': {
      entries: [],
      hasMore: false,
      loading: true,
      error: null,
    },
  };

  const rows = flattenTree(
    'viking://root',
    0,
    dirs,
    new Set(['viking://root', 'viking://root/slow']),
  );

  assert.deepEqual(
    rows.map((row) => row.kind),
    ['entry', 'loading'],
  );
  assert.equal(rows[1].parentId, 'viking://root/slow');
  assert.equal(rows[1].depth, 1);
});

test('an expanded directory with no children gets an empty row', () => {
  const dirs = {
    'viking://root': {
      entries: [{ uri: 'viking://root/empty-dir', isDir: true }],
      hasMore: false,
      loading: false,
      error: null,
    },
    'viking://root/empty-dir': {
      entries: [],
      hasMore: false,
      loading: false,
      error: null,
    },
  };

  const rows = flattenTree(
    'viking://root',
    0,
    dirs,
    new Set(['viking://root', 'viking://root/empty-dir']),
  );

  assert.deepEqual(
    rows.map((row) => row.kind),
    ['entry', 'empty'],
  );
  // 空态行挂在被展开的那个目录下，缩进比它深一级。
  assert.equal(rows[1].parentId, 'viking://root/empty-dir');
  assert.equal(rows[1].depth, 1);
});
