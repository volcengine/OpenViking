import assert from 'node:assert/strict';
import test from 'node:test';

import { clampResizableWidth } from '../src/shared/components/resizable/clamp.ts';
import {
  TREE_DEFAULT_WIDTH,
  TREE_MIN_WIDTH,
  resolveTreeMaxWidth,
} from '../src/modules/file-browser/file-browser-layout.ts';

test('a width is clamped into the bounds and rounded', () => {
  assert.equal(clampResizableWidth(320.4, 200, 520), 320);
  assert.equal(clampResizableWidth(120, 200, 520), 200);
  assert.equal(clampResizableWidth(900, 200, 520), 520);
});

test('the minimum wins when the container leaves no room for the maximum', () => {
  // 容器只剩 300px 时上限算出来是负数，面板仍应保持可读的最小宽度。
  assert.equal(clampResizableWidth(400, 200, 100), 200);
  assert.equal(resolveTreeMaxWidth(300), TREE_MIN_WIDTH);
});

test('the tree cap leaves the detail pane its readable width', () => {
  // 800px 容器：上限 = 800 - 320(详情) - 24(分隔条)。
  assert.equal(resolveTreeMaxWidth(800), 456);
  // 宽容器下不越过 520px 的绝对上限。
  assert.equal(resolveTreeMaxWidth(1600), 520);
  // 首帧还没测出宽度时用绝对上限，不把树压到最小。
  assert.equal(resolveTreeMaxWidth(0), 520);
  assert.equal(TREE_DEFAULT_WIDTH, 352);
});
