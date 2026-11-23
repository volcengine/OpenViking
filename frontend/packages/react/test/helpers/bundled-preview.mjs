import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { rsbuild } from '@rslib/core';
import { pluginReact } from '@rsbuild/plugin-react';
import { JSDOM, ResourceLoader } from 'jsdom';

/** Exercise lazy browser renderers through the bundled public preview entry. */
export async function withBundledPreview(fixture, run) {
  const output = await mkdtemp(join(tmpdir(), 'ov-preview-'));
  let dom;
  try {
    const builder = await rsbuild.createRsbuild({
      cwd: fileURLToPath(new URL('../../../..', import.meta.url)),
      rsbuildConfig: {
        mode: 'production',
        plugins: [pluginReact()],
        performance: { printFileSize: false, buildCache: false },
        source: {
          entry: { index: `./packages/react/test/fixtures/${fixture}.tsx` },
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
    return await run(dom);
  } finally {
    dom?.window.close();
    await rm(output, { recursive: true, force: true });
  }
}
