import assert from 'node:assert/strict';
import test from 'node:test';

import {
  flattenResult,
  normalizeGrepResult,
} from '../src/modules/file-browser/search-model.ts';

test('grep matches in one file stay separate rows', () => {
  // 同一文件的多行命中是不同的结果，各自带自己的行号；按 URI 合并会把第 2 行
  // 之后的命中全部吞掉。
  //
  // Several matches in one file are distinct results, each with its own line; merging
  // by URI would swallow every match after the first.
  const result = normalizeGrepResult([
    { uri: 'viking://a.md', line: 1, content: 'TODO one' },
    { uri: 'viking://a.md', line: 9, content: 'TODO two' },
    { uri: 'viking://b.md', line: 3, content: 'TODO three' },
  ]);

  const rows = flattenResult(result);
  assert.equal(rows.length, 3);
  assert.deepEqual(
    rows.map((hit) => `${hit.uri}:${hit.line}`),
    ['viking://a.md:1', 'viking://a.md:9', 'viking://b.md:3'],
  );
});
