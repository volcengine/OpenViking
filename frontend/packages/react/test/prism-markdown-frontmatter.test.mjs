import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const languageModule = new URL(
  '../src/shared/prism-language.ts',
  import.meta.url,
);

for (const registered of [false, true]) {
  test(`Markdown highlights YAML front matter with a ${registered ? 'shared' : 'cold'} grammar`, () => {
    // Prism registers grammars globally, so each load order needs a fresh process.
    const run = spawnSync(
      process.execPath,
      [
        '--input-type=module',
        '--eval',
        String.raw`
          import assert from 'node:assert/strict';
          import { loadPrismLanguage, Prism } from ${JSON.stringify(languageModule.href)};

          if (${registered}) await import('prismjs/components/prism-markdown.js');
          const shared = Prism.languages.markdown;
          assert.equal(Prism.languages.yaml, undefined);

          const grammar = await loadPrismLanguage('markdown');
          if (shared) assert.equal(grammar, shared);
          const content = '---\nname: Guide\nversion: 7\n---\n\n# Body';
          const highlighted = Prism.highlight(content, grammar, 'markdown');
          assert.match(highlighted, /class="token key atrule">name</);
          assert.match(highlighted, /class="token number">7</);
          assert.match(highlighted, /class="token title important">/);
        `,
      ],
      { cwd: new URL('.', import.meta.url), encoding: 'utf8', timeout: 10_000 },
    );
    assert.ifError(run.error);
    assert.equal(run.status, 0, run.stderr);
  });
}
