import assert from 'node:assert/strict';
import test from 'node:test';
import { metadataFixture } from './helpers/content-metadata-fixture.mjs';

for (const extension of ['yaml', 'yml']) {
  for (const quote of ["'", '"']) {
    for (const newline of ['\n', '\r\n', '\r']) {
      for (const payload of ['{}', '{\n  "version": 3\n}']) {
        test(`hide trailers after an unclosed ${quote} scalar in ${extension}, ${JSON.stringify(newline)}, ${payload.length}`, async () => {
          const body = `example: ${quote}unterminated`;
          const content = [body, '', '<!-- MEMORY_FIELDS', payload, '-->']
            .join('\n')
            .replace(/\n/g, newline);
          const f = await metadataFixture({
            uri: `viking://user/alice/memories/preferences/example.${extension}`,
            content,
          });
          try {
            assert.equal(f.tab.source.content, body);
            assert.equal(await f.tab.editing.loadContent(), body);
            await f.tab.editing.save(body);
            assert.equal(
              f.fetch.calls.find((call) => call.body).body.content,
              body,
            );
          } finally {
            await f.view.unmount();
          }
        });
      }
    }
  }
}
