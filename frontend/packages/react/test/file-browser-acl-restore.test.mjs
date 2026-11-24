import assert from 'node:assert/strict';
import test from 'node:test';
import {
  button,
  click,
  fixture,
  initial,
  mount,
  ok,
  open,
  tree,
  writes,
} from './helpers/acl-dom.mjs';

for (const [name, inherited, warn] of [
  ['same user loses manage', [['user:bob', 'read']], true],
  ['all users only grants read', [['user:*', 'read']], true],
  ['same user retains manage', [['user:bob', 'manage']], false],
  ['all users retains manage', [['user:*', 'manage']], false],
  [
    'stronger wildcard covers a weaker exact grant',
    [
      ['user:bob', 'read'],
      ['user:*', 'manage'],
    ],
    false,
  ],
  [
    'unrelated inherited user does not cover Bob',
    [['user:alice', 'manage']],
    true,
  ],
]) {
  test(`restore confirmation: ${name}`, async () => {
    const direct = [{ principal: 'user:bob', level: 'manage' }];
    const report = {
      ...initial(),
      acl_mode: 'restricted',
      direct_entries: direct,
      effective_entries: direct,
      inherited_entries: inherited.map(([principal, level]) => ({
        principal,
        level,
      })),
    };
    const fetch = fixture((call) =>
      call.path.endsWith('/acl') && call.init.method === 'GET'
        ? ok(report)
        : undefined,
    );
    const view = await mount(tree(fetch));
    try {
      await open();
      await click(button('Restore inheritance'));
      const confirmation = document.querySelector(
        '.ov-file-browser-acl__confirmation',
      );
      assert.equal(confirmation.textContent.includes('user:bob'), warn);
      assert.equal(writes(fetch).length, 0);
      await click(button('Cancel', confirmation));
      assert.equal(writes(fetch).length, 0);
    } finally {
      await view.unmount();
    }
  });
}
