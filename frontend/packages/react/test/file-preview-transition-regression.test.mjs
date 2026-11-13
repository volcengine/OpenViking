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

/**
 * 按 JSDOM 的取消契约返回请求。
 *
 * `ResourceLoader.fetch` 返回的对象要带 `abort()`：`window.close()` 会让
 * `RequestManager` 对在途请求调用它。只返回一个普通 Promise 时，脚本尚未加载完
 * 就关闭窗口会抛 `TypeError: openedRequest.abort is not a function`，把一次正常
 * 清理变成测试失败。`abort()` 把该 Promise 标成已取消，后续结果不再交付。
 *
 * Returns a request that honours JSDOM's cancellation contract. The object
 * `ResourceLoader.fetch` returns needs `abort()`: `window.close()` calls it on every
 * in-flight request. A bare Promise throws `TypeError: openedRequest.abort is not a
 * function` when the window closes before scripts finish loading, turning ordinary
 * cleanup into a test failure.
 */
function cancellable(read) {
  let aborted = false;
  const promise = read().then((value) => {
    if (aborted) throw new Error('aborted');
    return value;
  });
  promise.abort = () => {
    aborted = true;
  };
  return promise;
}

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
          ? cancellable(() => readFile(join(output, pathname)))
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

test('a layer keeps the body it last showed once it steps aside', async () => {
  // B 以占位进入、补上正文并显示，再切到异步的 C：屏幕上应仍是 B 的正文，而不是
  // 它创建时的占位。槽位因此要保存最后实际显示的那份 tab。
  //
  // B enters as a placeholder, receives its body, shows it, then C is selected: the
  // screen must still show B's body rather than B's creation-time placeholder, so the
  // slot has to keep the tab it last actually rendered.
  const dom = await open('snapshot');
  try {
    const { document } = dom.window;
    const level = (key) =>
      Array.from(document.querySelectorAll('.ov-file-preview__level')).find(
        (button) => button.textContent === key,
      );
    assert.ok(await until(() => level('L1')), 'the level tabs must render');
    level('L1').click();
    // 等宽限期过完、占位出现，再补上正文，这样 B 的正文是「后来才到达」的。
    // Let the grace period elapse so the placeholder shows, then supply the body — B's
    // body arrives late, which is the case that exposed the stale snapshot.
    await until(() => overlayOf(document), 2000);
    dom.window.__setSnapshotReady?.();
    // 等 B 的正文真正显示出来。
    // Wait until B's body is actually on screen.
    assert.ok(
      await until(
        () =>
          document.body.textContent.includes('B body') && !overlayOf(document),
        4000,
      ),
      'B must show its body before switching away',
    );
    // 切到 C：B 退居后台。屏幕上任何一帧都应是 B 的正文，绝不能退回它创建时的
    // 占位；C 就绪后换成 C 也正常。
    //
    // Switch to C: B steps aside. Every frame must show B's body, never its
    // creation-time placeholder; C taking over once ready is expected.
    level('L2').click();
    const visibleText = () =>
      Array.from(document.querySelectorAll('.ov-file-preview__layer'))
        .filter((layer) => layer.getAttribute('aria-hidden') === null)
        .map((layer) => layer.textContent)
        .join('|');
    // 连续采样到 C 出现为止。B 退居后台期间，屏幕上要么是 B 的正文，要么是 C；
    // 绝不能退回它创建时的占位——那份占位在真实正文到达前是 loading 态，退回去
    // 会显示「加载中」，正是这条意见指出的问题。
    //
    // Sample until C appears. While B is in the background the screen shows either B's
    // body or C — never its creation-time placeholder, which was still loading before
    // the real body arrived and would show "Loading…" — exactly what review flagged.
    let sawBody = false;
    for (let attempt = 0; attempt < 100; attempt++) {
      const shown = visibleText();
      assert.doesNotMatch(
        shown,
        /B placeholder|Loading/,
        'a layer that stepped aside must not fall back to its creation-time placeholder',
      );
      if (shown.includes('B body')) sawBody = true;
      if (shown.includes('C body')) break;
      await delay(10);
    }
    assert.ok(sawBody, 'B body must stay on screen while C loads');
  } finally {
    dom.window.close();
  }
});
