import assert from 'node:assert/strict';
import test from 'node:test';
import { metadataFixture } from './helpers/content-metadata-fixture.mjs';

const root = 'viking://user/alice';
const contexts = [
  [`${root}/memories/preferences/examples.md`, 'MEMORY_FIELDS'],
  [`${root}/resources/handbook/examples.md`, 'CONFLICTS_CONTENT'],
];

async function assertProjection(uri, content, expected = content) {
  const f = await metadataFixture({ uri, content });
  try {
    assert.equal(f.tab.source.content, expected);
    assert.equal(await f.tab.editing.loadContent(), expected);
    await f.tab.editing.save(expected);
    assert.equal(
      f.fetch.calls.find((call) => call.body).body.content,
      expected,
    );
  } finally {
    await f.view.unmount();
  }
}

for (const [uri, marker] of contexts) {
  for (const indent of ['    ', '\t', '  \t']) {
    for (const close of ['', ' -->']) {
      test(`preserve ${JSON.stringify(indent)} code with ${close ? 'closed' : 'unclosed'} ${marker}`, async () => {
        await assertProjection(
          uri,
          `# Example\n\n${indent}<!-- ${marker} {example}${close}\n`,
        );
      });
    }
    test(`filter a real ${marker} trailer after ${JSON.stringify(indent)} code`, async () => {
      const body = `# Example\n\n${indent}<!-- ${marker} {example}\n\n`;
      await assertProjection(
        uri,
        body + `<!-- ${marker}\n{}\n-->`,
        marker === 'MEMORY_FIELDS' ? body.trimEnd() : body,
      );
    });
  }
  for (const newline of ['\n', '\r\n', '\r']) {
    for (const ending of ['---', '...']) {
      test(`preserve ${marker} inside ${JSON.stringify(newline)} YAML ending ${ending}`, async () => {
        const content = [
          '---',
          'example: |',
          `  <!-- ${marker}`,
          ending,
          '# Body',
          '',
        ].join(newline);
        await assertProjection(uri, content);
      });
      test(`filter a real ${marker} trailer after ${JSON.stringify(newline)} YAML ending ${ending}`, async () => {
        const header = [
          '---',
          'example: |',
          `  <!-- ${marker}`,
          ending,
          '# Body',
          '',
          '',
        ].join(newline);
        const trailer = `<!-- ${marker}${newline}{}${newline}-->`;
        const expected = marker === 'MEMORY_FIELDS' ? header.trimEnd() : header;
        await assertProjection(uri, header + trailer, expected);
      });
    }
  }
}
