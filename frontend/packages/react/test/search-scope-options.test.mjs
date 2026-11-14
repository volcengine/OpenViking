import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildScopeOptions,
  isSearchScopeAllowed,
  normalizeFilters,
} from '../src/modules/file-browser/search-model.ts';

const HOME = 'viking://~';
const ALL = [
  `${HOME}/resources`,
  'viking://resources',
  `${HOME}/skills`,
  'viking://agent/skills',
];

test('a broad root offers every retrievable scope', () => {
  assert.deepEqual(
    buildScopeOptions('viking://').map((option) => option.uri),
    ALL,
  );
});

test('a narrow root offers itself rather than the scope covering it', () => {
  // 候选若是祖先，勾选后 target_uri 会从项目根换成整个资源库，检索范围被悄悄
  // 放大，返回的兄弟目录资源树里也定位不到。
  //
  // Offering the ancestor would swap target_uri from the project root to the whole
  // library, silently widening the search and returning siblings the tree cannot locate.
  assert.deepEqual(
    buildScopeOptions('viking://resources/project-a').map(
      (option) => option.uri,
    ),
    ['viking://resources/project-a'],
  );
  assert.deepEqual(
    buildScopeOptions(`${HOME}/resources/project-b`).map(
      (option) => option.uri,
    ),
    [`${HOME}/resources/project-b`],
  );
});

test('a root outside every retrievable scope offers nothing', () => {
  assert.deepEqual(buildScopeOptions('viking://agent/tools'), []);
  assert.deepEqual(buildScopeOptions('viking://unknown'), []);
});

test('a directory picked within the current root cannot carry over to a sibling root', () => {
  const selected = 'viking://resources/project-a/docs';
  assert.equal(
    isSearchScopeAllowed(
      buildScopeOptions('viking://resources/project-a'),
      selected,
    ),
    true,
  );
  assert.equal(
    isSearchScopeAllowed(
      buildScopeOptions('viking://resources/project-ab'),
      selected,
    ),
    false,
  );
  assert.equal(
    isSearchScopeAllowed(
      buildScopeOptions('viking://resources/project-a/docs/deeper'),
      selected,
    ),
    false,
  );
});

test('switching to a single-select mode collapses the scopes to one', () => {
  // 两项都留着时，单选控件只能勾中一个、请求也只取第一个，界面与请求脱节。
  //
  // With both kept, the single-select control shows one and the request takes the
  // first, so the display and the request disagree.
  const filters = {
    scopes: ['viking://resources', 'viking://agent/skills'],
    levels: [0, 2],
    timeField: 'updated_at',
    since: '2026-11-01',
    until: '2026-11-30',
    caseInsensitive: true,
  };

  const single = normalizeFilters(filters, 'grep');
  assert.deepEqual(single.scopes, ['viking://resources']);
  assert.deepEqual(single.levels, []);
  assert.equal(single.timeField, '');
  assert.equal(single.since, '');
  assert.equal(single.until, '');
  assert.equal(single.caseInsensitive, true);
});

test('switching to a semantic mode clears the grep-only flag and keeps arrays', () => {
  const filters = {
    scopes: ['viking://resources'],
    levels: [1],
    timeField: 'created_at',
    since: '2026-11-01',
    until: '',
    caseInsensitive: true,
  };

  const semantic = normalizeFilters(filters, 'find');
  assert.deepEqual(semantic.scopes, ['viking://resources']);
  assert.deepEqual(semantic.levels, [1]);
  assert.equal(semantic.since, '2026-11-01');
  assert.equal(semantic.caseInsensitive, false);
});
