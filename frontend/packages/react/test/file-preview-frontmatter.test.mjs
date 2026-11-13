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

test('YAML flow mappings and sequences are fenced without rewriting the source', () => {
  for (const metadata of [
    '{"title":"Guide","version":1}\n',
    '# preview metadata\n{title: Guide,\n  tags: [docs, preview]}\n',
    '[Guide, {version: 1}]\n',
  ]) {
    assert.equal(
      fenceMarkdownFrontmatter(`---\n${metadata}---\n\n# Body`),
      `~~~yaml\n${metadata}~~~\n\n\n# Body`,
      metadata,
    );
  }
});

test('ordinary Markdown rules and invalid or incomplete metadata remain unchanged', () => {
  for (const content of [
    '---\nPlain prose\n---\nText',
    '---\n[Guide](https://example.com)\n---\n\n# Body',
    '---\nNote: this is prose.\n\nAnother paragraph.\n---\nText',
    '---\n{title: Guide}\n\nAnother paragraph.\n---\nText',
    '---\n[docs, preview\n---\nText',
    '---\ntitle: draft',
    '# Title\n---\ntitle: body\n---',
  ])
    assert.equal(fenceMarkdownFrontmatter(content), content, content);
});
