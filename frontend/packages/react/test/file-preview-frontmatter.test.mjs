import assert from 'node:assert/strict';
import test from 'node:test';
import { fenceMarkdownFrontmatter } from '../src/components/file-preview/file-preview-frontmatter.ts';

test('leading metadata is fenced without changing the following Markdown table', () => {
  const table = '| Name | Count |\r\n| --- | --- |\r\n| Item | 3 |';
  const metadata = 'name: Guide\r\nnotes: |\r\n  ~~~~\r\n';
  const input = `\uFEFF---\r\n${metadata}---\r\n${table}`;
  assert.equal(
    fenceMarkdownFrontmatter(input),
    `~~~~~yaml\n${metadata}~~~~~\n\n${table}`,
  );
});

test('prose between rules and incomplete metadata remain unchanged', () => {
  for (const content of [
    '---\nPlain prose\n---\nText',
    '---\ntitle: draft',
    '# Title\n---\ntitle: body\n---',
  ])
    assert.equal(fenceMarkdownFrontmatter(content), content);
});
