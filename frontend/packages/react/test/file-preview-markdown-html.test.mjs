import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import test from 'node:test';
import { withBundledPreview } from './helpers/bundled-preview.mjs';

test('FilePreview sanitizes Markdown HTML and resolves imported images in mixed HTML blocks', () =>
  withBundledPreview('file-preview-markdown-html', async (dom) => {
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
  }));
