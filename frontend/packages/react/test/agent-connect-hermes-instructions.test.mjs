import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRslib } from '@rslib/core';

const output = await mkdtemp(join(tmpdir(), 'ov-hermes-instructions-'));
after(() => rm(output, { recursive: true, force: true }));
const builder = await createRslib({
  config: {
    performance: { buildCache: false },
    lib: [
      {
        format: 'esm',
        dts: false,
        source: {
          entry: {
            instructions: fileURLToPath(
              new URL(
                '../src/components/agent-connect/agent-connect-instructions.ts',
                import.meta.url,
              ),
            ),
          },
        },
      },
    ],
    output: { target: 'node', distPath: { root: output } },
  },
});
await builder.build();
await writeFile(join(output, 'package.json'), '{"type":"module"}');
const {
  DEFAULT_BASE_URL,
  getAgentConnectContent,
  getAgentConnectInstructions,
} = await import(pathToFileURL(join(output, 'instructions.js')));

for (const [locale, language] of [
  ['en-US', 'en'],
  ['zh-CN', 'zh'],
]) {
  test(`Hermes custom instructions handle old snapshots and updated ${locale} guides`, async () => {
    const content = getAgentConnectContent(locale).hermes;
    const original = content.markdown;
    const updated = await readFile(
      new URL(
        `../../../../docs/images/agents/${language}/hermes.md`,
        import.meta.url,
      ),
      'utf8',
    );
    const customUrl = 'https://custom.example/openviking';
    const apiKey = 'synthetic-hermes-key';
    const step = (value) => value.match(/^3\..*$/m)?.[0];
    try {
      for (const markdown of [original, updated]) {
        content.markdown = markdown;
        const custom = getAgentConnectInstructions(
          'hermes',
          locale,
          apiKey,
          customUrl,
        );
        assert.match(step(custom.copy), /\*\*Custom\*\*/);
        assert.ok(step(custom.copy).includes(customUrl));
        assert.ok(custom.copy.includes(apiKey));
        assert.ok(!custom.display.includes(apiKey));
        assert.ok(custom.display.includes('********'));

        const managed = getAgentConnectInstructions(
          'hermes',
          locale,
          apiKey,
          DEFAULT_BASE_URL,
        );
        assert.equal(step(managed.copy), step(markdown));
      }
    } finally {
      content.markdown = original;
    }
  });
}
