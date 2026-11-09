import assert from 'node:assert/strict';
import test from 'node:test';

import { initialLevelKey } from '../src/components/file-preview/level-selection.ts';

const levels = [{ key: 'l0' }, { key: 'l1' }];

test('an uncontrolled directory tab honours defaultLevelKey', () => {
  // 层级状态提升到外壳之后，外壳算出的 `activeLevelKey` 是受控值，正文再也没有
  // 机会用上自己的初值；因此外壳必须自己尊重 `defaultLevelKey`。
  //
  // Once the level state moved into the shell, the `activeLevelKey` it computes is a
  // controlled value and the body never applies its own initial one, so the shell has
  // to honour `defaultLevelKey` itself.
  assert.equal(
    initialLevelKey({ levels, defaultLevelKey: 'l1' }, {}, 'dir'),
    'l1',
  );
});

test('the controlled value wins over a remembered or default choice', () => {
  assert.equal(
    initialLevelKey(
      { levels, defaultLevelKey: 'l1', activeLevelKey: 'l0' },
      { dir: 'l1' },
      'dir',
    ),
    'l0',
  );
});

test('a remembered choice wins over the default', () => {
  assert.equal(
    initialLevelKey({ levels, defaultLevelKey: 'l0' }, { dir: 'l1' }, 'dir'),
    'l1',
  );
});

test('the first level is the fallback, and an unknown key falls back too', () => {
  assert.equal(initialLevelKey({ levels }, {}, 'dir'), 'l0');
  assert.equal(
    initialLevelKey({ levels, defaultLevelKey: 'missing' }, {}, 'dir'),
    'l0',
  );
});
