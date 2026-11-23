import assert from 'node:assert/strict';
import test from 'node:test';
import { metadataFixture } from './helpers/content-metadata-fixture.mjs';

const root = 'viking://user/alice';
const memory = `${root}/memories/preferences/example`;
const wiki = `${root}/resources/handbook/example.md`;

for (const [extension, content] of [
  ['json', '{"example":"<!-- MEMORY_FIELDS"}'],
  ['jsonl', '{"example":"<!-- MEMORY_FIELDS"}\n'],
  ['yaml', 'example: |\n  <!-- MEMORY_FIELDS\n'],
]) {
  test(`an unclosed marker is literal data in a memory ${extension} file`, async () => {
    const f = await metadataFixture({ uri: `${memory}.${extension}`, content });
    try {
      assert.equal(f.tab.source.content, content);
      assert.equal(await f.tab.editing.loadContent(), content);
    } finally {
      await f.view.unmount();
    }
  });
}

for (const [uri, marker] of [
  [`${memory}.md`, 'MEMORY_FIELDS'],
  [wiki, 'CONFLICTS_CONTENT'],
]) {
  for (const content of [
    `# Examples\n\nWrite \`<!-- ${marker}\` to document the protocol.\n`,
    `---\nexample: |\n  <!-- ${marker}\n---\n# Body\n`,
    `---\nexample: |\n  \`\`\`\n---\n# Body\n\n<!-- ${marker} {} -->`,
  ]) {
    test(`quoted metadata stays in its document context at ${uri}: ${JSON.stringify(content)}`, async () => {
      const footer = content.endsWith('-->') ? `<!-- ${marker} {} -->` : '';
      const body = footer ? content.slice(0, -footer.length) : content;
      const expected = footer && uri !== wiki ? body.trimEnd() : body;
      const f = await metadataFixture({ uri, content });
      try {
        assert.equal(f.tab.source.content, expected);
        assert.equal(await f.tab.editing.loadContent(), expected);
      } finally {
        await f.view.unmount();
      }
    });
  }
}
