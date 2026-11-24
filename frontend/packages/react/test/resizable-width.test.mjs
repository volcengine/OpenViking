import assert from 'node:assert/strict';
import test from 'node:test';

import { clampResizableSize } from '../src/shared/primitives/resizable/clamp.ts';
import {
  STACKED_TREE_MIN_HEIGHT,
  TREE_DEFAULT_WIDTH_BY_LOCALE,
  TREE_MIN_WIDTH,
  resolveStackedTreeMaxHeight,
  resolveTreeMaxWidth,
} from '../src/modules/file-browser/file-browser-layout.ts';

test('a size is clamped into the bounds and rounded', () => {
  assert.equal(clampResizableSize(320.4, 200, 520), 320);
  assert.equal(clampResizableSize(120, 200, 520), 200);
  assert.equal(clampResizableSize(900, 200, 520), 520);
});

test('the minimum wins when the container leaves no room for the maximum', () => {
  // 容器只剩 300px 时上限算出来是负数，面板仍应保持可读的最小宽度。
  assert.equal(clampResizableSize(400, 200, 100), 200);
  assert.equal(resolveTreeMaxWidth(300), TREE_MIN_WIDTH);
});

test('a stacked tree can resize only when both panes keep their minimum height', () => {
  assert.equal(STACKED_TREE_MIN_HEIGHT, 160);
  assert.equal(resolveStackedTreeMaxHeight(0), undefined);
  assert.equal(resolveStackedTreeMaxHeight(400), undefined);
  assert.equal(resolveStackedTreeMaxHeight(401), undefined);
  assert.equal(resolveStackedTreeMaxHeight(402), 161);
  assert.equal(resolveStackedTreeMaxHeight(790), 549);
});

test('the tree cap leaves the detail pane its readable width', () => {
  // 800px 容器：上限 = 800 - 320(详情) - 1(分隔线)。
  assert.equal(resolveTreeMaxWidth(800), 479);
  // 宽容器下不越过 520px 的绝对上限。
  assert.equal(resolveTreeMaxWidth(1600), 520);
  // 首帧还没测出宽度时用绝对上限，不把树压到最小。
  assert.equal(resolveTreeMaxWidth(0), 520);
  assert.deepEqual(TREE_DEFAULT_WIDTH_BY_LOCALE, {
    'zh-CN': 352,
    'en-US': 500,
  });
});
