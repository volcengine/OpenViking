import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import test from 'node:test';
import { rsbuild } from '@rslib/core';
import { pluginReact } from '@rsbuild/plugin-react';
import { JSDOM, ResourceLoader } from 'jsdom';

test('FilePreview sanitizes Markdown HTML and resolves imported images in mixed HTML blocks', async () => {
  const output = await mkdtemp(join(tmpdir(), 'ov-markdown-preview-'));
  let dom;
  try {
    // Bundle the public entry as a browser consumer. The lazy Ark renderers
    // import browser assets and cannot run through Node's ESM loader.
    const builder = await rsbuild.createRsbuild({
      cwd: fileURLToPath(new URL('../../..', import.meta.url)),
      rsbuildConfig: {
        mode: 'production',
        plugins: [pluginReact()],
        performance: { printFileSize: false },
        source: {
          entry: {
            index:
              './packages/react/test/fixtures/file-preview-markdown-html.tsx',
          },
        },
        output: { distPath: { root: output }, cleanDistPath: false },
      },
    });
    await builder.build();
    dom = new JSDOM(await readFile(join(output, 'index.html'), 'utf8'), {
      url: 'https://preview.test/',
      runScripts: 'dangerously',
      pretendToBeVisual: true,
      resources: new (class extends ResourceLoader {
        fetch(url) {
          const { origin, pathname } = new URL(url);
          return origin === 'https://preview.test' &&
            pathname.startsWith('/static/js/')
            ? super.fetch(pathToFileURL(join(output, pathname)).href)
            : null;
        }
      })(),
      beforeParse(window) {
        window.TextEncoder = class extends TextEncoder {
          encode(text) {
            return window.Uint8Array.from(super.encode(text));
          }
        };
        window.TextDecoder = TextDecoder;
        window.structuredClone = structuredClone;
        window.Blob = Blob;
        window.ResizeObserver = class {
          observe() {}
          disconnect() {}
        };
        window.matchMedia = () => ({
          matches: false,
          addEventListener() {},
          removeEventListener() {},
        });
      },
    });
    const { document } = dom.window;
    const names = [
      'with-caption',
      'second-block',
      'third-block',
      'readme-logo',
    ];
    const expected = names.map(
      (name) =>
        `https://preview.test/resolved/${encodeURIComponent(`viking://resources/${name}.png`)}`,
    );
    const images = () => [...document.querySelectorAll('img')];
    for (let attempt = 0; attempt < 400; attempt++) {
      if (expected.every((src) => images().some((image) => image.src === src)))
        break;
      await delay(20);
    }
    assert.equal(document.querySelector('strong')?.textContent, 'Safe HTML');
    assert.ok(document.querySelector('a[href="https://example.com/"]'));
    assert.deepEqual(
      images().map((img) => img.alt),
      ['with-caption', 'Second "quoted" & <label>', 'third-block', 'image'],
    );
    assert.deepEqual(
      images().map((img) => img.src),
      expected,
    );
    assert.match(document.body.textContent, /Caption after the first image\./);
    assert.match(document.body.textContent, /OpenViking/);
    assert.match(document.querySelector('pre')?.textContent, /!\[literal\]/);
    assert.equal(
      document.querySelector('kbimage, [onclick], a[href^="javascript:"]'),
      null,
    );
    assert.equal(document.querySelector('#root script'), null);
    assert.doesNotMatch(
      document.body.textContent,
      /untrustedScript|!\[(?:with-caption|second-block|third-block|image)\]/,
    );
    assert.equal(dom.window.untrustedScript, undefined);
  } finally {
    dom?.window.close();
    await rm(output, { recursive: true, force: true });
  }
});
