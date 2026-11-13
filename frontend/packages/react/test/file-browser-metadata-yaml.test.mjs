import assert from 'node:assert/strict';
import test from 'node:test';
import { metadataFixture } from './helpers/content-metadata-fixture.mjs';

const marker = '<!-- MEMORY_FIELDS {} -->';
const trailer = '\n\n<!-- MEMORY_FIELDS\n{\n  "version": 3\n}\n-->';
const examples = [
  ['plain', `example: ${marker}\n`],
  ['literal block', `example: |\n  ${marker}\n`],
  ['literal block with kept blank lines', `example: |+\n  ${marker}\n\n`],
  ['CRLF literal block', `example: |\r\n  ${marker}\r\n`],
  ['CR literal block', `example: |\r  ${marker}\r`],
  ['folded block', `example: >-\n  ${marker}\n`],
  ['sequence', `- ${marker}\n`],
  ['nested sequence', `examples:\n  - ${marker}\n`],
  ['quoted', `example: '${marker}'\n`],
  ['Unicode prefix', `label: 雪😀\nexample: ${marker}\n`],
  ['multi-document', `other: true\n---\nexample: ${marker}\n`],
  ['unrelated syntax error', `other: [\nexample: ${marker}\n`],
  ['preview cap', 'example: |\n' + '  line\n'.repeat(1998) + `  ${marker}\n`],
];

for (const extension of ['yaml', 'yml']) {
  for (const [name, source] of examples) {
    for (const suffix of ['', trailer, '\n\n' + marker + trailer]) {
      test(`preserve ${name} scalar in ${extension}, ${suffix ? 'with' : 'without'} real trailers (${suffix.length})`, async () => {
        const expected = suffix ? source.trimEnd() : source;
        const f = await metadataFixture({
          uri: `viking://user/alice/memories/preferences/example.${extension}`,
          content: source + suffix,
        });
        try {
          assert.equal(f.tab.source.content, expected);
          assert.equal(await f.tab.editing.loadContent(), expected);
          await f.tab.editing.save(expected);
          const write = f.fetch.calls.find((call) => call.body).body;
          assert.equal(write.content, expected);
          assert.equal(write.edit_source, 'manual');
          assert.equal('resolve_conflicts' in write, false);
        } finally {
          await f.view.unmount();
        }
      });
    }
  }
}
