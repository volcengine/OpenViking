import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { rsbuild } from '@rslib/core';
import { pluginReact } from '@rsbuild/plugin-react';
import { JSDOM, ResourceLoader } from 'jsdom';

// 过渡层的两个边界都来自评审：切回屏幕上已有层级时必须取消整轮过渡，后台槽位
// 要跟随同一身份的最新页签。两条都要求真实渲染器跑起来，因此和 markdown-html
// 那条一样先打包再交给 JSDOM。
//
// Both edges come from review: returning to the level already on screen must cancel
// the transition, and the background slot has to follow the latest tab of the same
// identity. Both need the real renderer, so this bundles first and hands the result to
// JSDOM, like the markdown-html test does.

const output = await mkdtemp(join(tmpdir(), 'ov-transition-'));
process.on('exit', () => {
  void rm(output, { recursive: true, force: true });
});

const builder = await rsbuild.createRsbuild({
  cwd: fileURLToPath(new URL('../../..', import.meta.url)),
  rsbuildConfig: {
    mode: 'production',
    plugins: [pluginReact()],
    performance: { printFileSize: false },
    source: {
      entry: {
        index: './packages/react/test/fixtures/file-preview-transition.tsx',
      },
    },
    output: { distPath: { root: output }, cleanDistPath: false },
  },
});
await builder.build();
const html = await readFile(join(output, 'index.html'), 'utf8');

/** 在给定场景下打开页面，返回窗口。 Opens the page for one case and returns its window. */
async function open(caseName) {
  const dom = new JSDOM(html, {
    url: `https://preview.test/?case=${caseName}`,
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    resources: new (class extends ResourceLoader {
      fetch(url) {
        const { origin, pathname } = new URL(url);
        return origin === 'https://preview.test' &&
          pathname.startsWith('/static/js/')
          ? readFile(join(output, pathname))
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
  return dom;
}

/** 等一个条件成立，最多 `ms` 毫秒。 Waits for a condition, up to `ms`. */
async function until(check, ms = 4000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (check()) return true;
    await delay(20);
  }
  return false;
}

const overlayOf = (document) =>
  document.querySelector('.ov-file-preview__layers-loading');
const layerCount = (document) =>
  document.querySelectorAll('.ov-file-preview__layer').length;

test('returning to the level already on screen cancels the transition', async () => {
  const dom = await open('cancel');
  try {
    const { document } = dom.window;
    const level = (key) =>
      Array.from(document.querySelectorAll('.ov-file-preview__level')).find(
        (button) => button.textContent === key,
      );
    assert.ok(await until(() => level('L0')), 'the level tabs must render');
    // 切到另一级并等过宽限期，遮罩露出。
    // Switch away and wait past the grace period so the overlay shows.
    level('L1').click();
    await until(() => overlayOf(document), 2000);
    // 切回屏幕上原本那一级：整轮过渡作废。
    // Return to the level already on screen: the transition is cancelled.
    level('L0').click();
    assert.ok(
      await until(() => !overlayOf(document), 2000),
      'the overlay must go away after returning to the visible level',
    );
    assert.ok(
      await until(() => layerCount(document) === 1, 2000),
      'the background slot must be dropped',
    );
  } finally {
    dom.window.close();
  }
});

test('the background slot follows the latest tab under the same level', async () => {
  const dom = await open('late');
  try {
    const { document } = dom.window;
    const level = (key) =>
      Array.from(document.querySelectorAll('.ov-file-preview__level')).find(
        (button) => button.textContent === key,
      );
    assert.ok(await until(() => level('L1')), 'the level tabs must render');
    // 切到 L1：宿主给的是 loading 的空正文，宽限期过后遮罩露出。
    // Switch to L1: the host supplies an empty body while loading, so the overlay shows
    // once the grace period ends.
    level('L1').click();
    assert.ok(
      await until(() => overlayOf(document), 2000),
      'the loading placeholder must show the overlay',
    );
    // 同一层级下补上正文：后台槽位要跟着换，遮罩才能撤掉。
    // The body arrives under the same level: the background slot has to follow so the
    // overlay can go away.
    dom.window.__setLateReady?.();
    assert.ok(
      await until(() => !overlayOf(document), 3000),
      'the overlay must clear once the body arrives',
    );
    assert.equal(layerCount(document), 1);
  } finally {
    dom.window.close();
  }
});
