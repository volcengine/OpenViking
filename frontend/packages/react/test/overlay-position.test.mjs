import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import './helpers/module-dom.mjs';

const { resolvePosition } = await import(
  pathToFileURL(`${process.env.OV_UI_TEST_HOOKS}/overlay-position.js`).href
);

test('overlay start alignment preserves the trigger left edge alongside center and end', () => {
  const trigger = new window.DOMRect(100, 100, 240, 32);
  const surface = { width: 300, height: 100 };
  for (const [alignment, left] of [
    ['start', 100],
    ['center', 70],
    ['end', 40],
  ]) {
    assert.deepEqual(resolvePosition(trigger, surface, 'bottom', alignment), {
      top: 138,
      left,
      placement: 'bottom',
    });
  }
});

test('start-aligned overlays flip above and clamp horizontally at viewport edges', () => {
  const trigger = new window.DOMRect(
    window.innerWidth - 70,
    window.innerHeight - 60,
    60,
    32,
  );
  const surface = { width: 200, height: 100 };
  assert.deepEqual(resolvePosition(trigger, surface, 'bottom', 'start'), {
    top: trigger.top - 106,
    left: window.innerWidth - 208,
    placement: 'top',
  });
});
