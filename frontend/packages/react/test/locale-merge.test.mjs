import assert from 'node:assert/strict';
import test from 'node:test';

import { mergeLabels } from '../src/shared/locale.ts';

test('a nested override keeps its siblings defaults', () => {
  // 宿主只改一个模式时，其余模式必须还在——否则占位符会变成 undefined。
  //
  // Overriding one mode must leave the others intact, or their placeholders turn
  // undefined.
  const defaults = {
    searchModePlaceholders: { find: 'a', grep: 'b', glob: 'c' },
    plain: 'keep',
  };
  const merged = mergeLabels(defaults, {
    searchModePlaceholders: { grep: 'B' },
  });

  assert.deepEqual(merged.searchModePlaceholders, {
    find: 'a',
    grep: 'B',
    glob: 'c',
  });
  assert.equal(merged.plain, 'keep');
});

test('arrays and functions replace wholesale rather than merging', () => {
  // 数组按值替换：逐项合并会让宿主没法缩短一个列表。
  //
  // Arrays replace by value: merging item by item would leave a host unable to shorten
  // a list.
  const defaults = {
    levels: ['L0', 'L1', 'L2'],
    format: (n) => `default ${n}`,
  };
  const next = (n) => `custom ${n}`;
  const merged = mergeLabels(defaults, { levels: ['only'], format: next });

  assert.deepEqual(merged.levels, ['only']);
  assert.equal(merged.format(1), 'custom 1');
});

test('undefined inherits the default and an empty string stays an override', () => {
  const defaults = { title: 'Default', hint: 'Hint' };
  const merged = mergeLabels(defaults, { title: '', hint: undefined });

  assert.equal(merged.title, '');
  assert.equal(merged.hint, 'Hint');
});

test('merging does not mutate the defaults', () => {
  const defaults = { nested: { a: '1', b: '2' } };
  mergeLabels(defaults, { nested: { a: 'X' } });

  assert.deepEqual(defaults.nested, { a: '1', b: '2' });
});
