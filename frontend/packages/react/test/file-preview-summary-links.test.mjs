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

test('summary links navigate through FileBrowser and standalone directory operations', async () => {
  const output = await mkdtemp(join(tmpdir(), 'ov-summary-links-'));
  let dom;
  try {
    const builder = await rsbuild.createRsbuild({
      cwd: fileURLToPath(new URL('../../..', import.meta.url)),
      rsbuildConfig: {
        mode: 'production',
        plugins: [pluginReact()],
        performance: { printFileSize: false },
        source: {
          entry: {
            index:
              './packages/react/test/fixtures/file-preview-summary-links.tsx',
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
        window.Response = Response;
        window.CSS = { escape: (value) => value };
        window.HTMLElement.prototype.scrollIntoView = () => {};
        window.ResizeObserver = class {
          observe() {}
          unobserve() {}
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
    const evidence = () => dom.window.summaryLinksEvidence;
    async function waitFor(predicate) {
      for (let attempt = 0; attempt < 400; attempt++) {
        if (predicate()) return;
        await delay(20);
      }
      assert.fail('Summary preview did not become ready');
    }
    const link = (section, label) =>
      [...document.querySelectorAll(`#${section} a`)].find(
        (node) => node.textContent === label,
      );
    await waitFor(
      () =>
        link('standalone', 'Absolute resource') &&
        link('browser', 'Relative child'),
    );
    link('standalone', 'Absolute resource').click();
    await waitFor(() => evidence().standalone.length === 1);
    assert.deepEqual(
      [...evidence().standalone],
      ['viking://resources/guide.md'],
    );
    document.querySelectorAll('#standalone .ov-file-preview__level')[1].click();
    await waitFor(() => link('standalone', 'Relative child'));
    link('standalone', 'Relative child').click();
    await waitFor(() => evidence().standalone.length === 2);
    assert.equal(evidence().standalone[1], 'child/.overview.md');

    link('browser', 'Web reference').click();
    await waitFor(() => evidence().opened.length === 1);
    assert.deepEqual(
      [...evidence().opened[0]],
      ['https://example.com/reference', '_blank', 'noopener,noreferrer'],
    );
    const readsBeforeAnchor = evidence().reads.length;
    link('browser', 'Same document').click();
    await waitFor(() => evidence().scrolled.length > 0);
    assert.equal(evidence().reads.length, readsBeforeAnchor);
    link('browser', 'Cross document').click();
    await waitFor(() => evidence().opened.length === 2);
    assert.equal(
      evidence().opened[1][0],
      'https://preview.test/other.md#details',
    );
    assert.equal(link('browser', 'Unsafe')?.getAttribute('href'), null);
    link('browser', 'Unsafe').click();
    assert.equal(evidence().opened.length, 2);
    assert.equal(evidence().reads.length, readsBeforeAnchor);
    link('browser', 'Malformed fragment').click();
    assert.equal(evidence().reads.length, readsBeforeAnchor);
    link('browser', 'Protocol relative').click();
    await waitFor(() => evidence().opened.length === 3);
    assert.equal(evidence().opened[2][0], 'https://example.com/reference');
    link('browser', 'Relative child').click();
    await waitFor(() =>
      evidence().reads.includes('viking://resources/docs/child'),
    );
    await waitFor(
      () =>
        document.querySelector(
          '#browser .ov-file-preview__level[aria-selected="true"]',
        )?.textContent === 'L1 Overview',
    );
    assert.equal(
      document
        .querySelector('#browser [data-file-tree-row-id][aria-selected="true"]')
        ?.getAttribute('data-file-tree-row-id'),
      'viking://resources/docs/child',
    );
    await waitFor(() => link('browser', 'Encoded resource'));
    link('browser', 'Encoded resource').click();
    await waitFor(() =>
      evidence().reads.includes('viking://resources/docs/child folder'),
    );
    await waitFor(() => link('browser', 'Absolute resource'));
    link('browser', 'Absolute resource').click();
    await waitFor(() =>
      evidence().reads.includes('viking://resources/guide.md'),
    );
    assert.equal(
      evidence().reads.some((uri) => uri.startsWith('https:')),
      false,
    );
  } finally {
    dom?.window.close();
    await rm(output, { recursive: true, force: true });
  }
});
