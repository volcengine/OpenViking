import './helpers/history-viewer-dom.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixture,
  tree,
  mount,
  target,
  openAction,
  workflow,
  act,
  ok,
  settle,
  button,
  click,
} from './helpers/preview-native-dom.mjs';
const file = `${target}/doc.md`;

test('directory history keeps a single busy indicator throughout file, version and content loading', async () => {
  const waiting = new Map();
  let active = false;
  const fetch = fixture(
    target,
    true,
    (call) => {
      if (
        active &&
        ((call.path.endsWith('/fs/ls') && call.uri === target) ||
          /\/snapshot\/(versions|show)$/.test(call.path))
      )
        return new Promise((resolve) =>
          waiting.set(call.path.split('/').at(-1), resolve),
        );
    },
    [{ to: target }],
  );
  const view = await mount(tree(fetch, target));
  const busy = () =>
    workflow().querySelectorAll('[role="status"][aria-busy="true"]');
  const release = async (key, response) => {
    assert.ok(waiting.has(key), key);
    await act(async () => waiting.get(key)(response));
    await settle();
  };
  try {
    active = true;
    await openAction('Version history');
    assert.equal(busy().length, 1);
    assert.ok(workflow().querySelector('.ov-file-history__body'));
    await release('ls', ok([{ uri: file, isDir: false }]));
    assert.equal(busy().length, 1);
    await release(
      'versions',
      ok({
        versions: [
          {
            version_id: 'v1',
            version_number: 1,
            snapshot_id: 's1',
            snapshot_path: file,
            created_at: 1794268800,
          },
        ],
      }),
    );
    assert.equal(busy().length, 1);
    assert.ok(
      workflow().querySelector('.ov-file-history__viewer [aria-busy="true"]'),
    );
    await release('show', new Response('# Ready'));
    await settle();
    assert.equal(busy().length, 0);
    assert.match(workflow().textContent, /Ready/);
  } finally {
    await view.unmount();
  }
});

for (const [field, label] of [
  ['from', 'Source directories'],
  ['skill', 'Compilation Skill'],
]) {
  test(`${field} picker is busy from opening until its initial directory path has loaded`, async () => {
    const parent = 'viking://resources/source';
    const chosen = `${parent}/deep`;
    const config = {
      to: target,
      from: [chosen],
      skill: chosen,
      auto_update: false,
      update_schedule: null,
    };
    let active = false;
    const waiting = new Map();
    const fetch = fixture(
      target,
      true,
      (call) => {
        if (call.path.endsWith('/compile/config')) return ok(config);
        if (
          !active &&
          call.path.endsWith('/fs/ls') &&
          call.uri === 'viking://resources'
        )
          return ok([
            { uri: target, isDir: true, tags: ['compile_target=true'] },
            { uri: parent, isDir: true },
          ]);
        if (active && call.path.endsWith('/fs/ls'))
          return new Promise((resolve) => waiting.set(call.uri, resolve));
      },
      [{ to: target }],
    );
    const view = await mount(tree(fetch, target));
    const picker = () =>
      document.querySelector('.ov-context-picker [role="dialog"]');
    const assertBusy = () => {
      assert.equal(
        picker()
          .querySelector('.ov-context-picker__content')
          .getAttribute('aria-busy'),
        'true',
      );
      assert.ok(picker().querySelector('[role="status"][aria-busy="true"]'));
      assert.equal(picker().querySelectorAll('[role="treeitem"]').length, 0);
    };
    const release = async (uri, entries) => {
      assert.ok(waiting.has(uri), uri);
      await act(async () => waiting.get(uri)(ok(entries)));
      await settle();
    };
    try {
      await openAction('Compile configuration');
      active = true;
      await click(workflow().querySelector(`[aria-label="${label}"]`));
      assertBusy();
      await release('viking://', [{ uri: 'viking://resources', isDir: true }]);
      assertBusy();
      await release(parent, [{ uri: chosen, isDir: true }]);
      assertBusy();
      await release(chosen, []);
      assert.equal(
        picker()
          .querySelector('.ov-context-picker__content')
          .getAttribute('aria-busy'),
        null,
      );
      assert.equal(
        picker().querySelector('[role="status"][aria-busy="true"]'),
        null,
      );
      assert.ok(picker().querySelectorAll('[role="treeitem"]').length);
      assert.equal(picker().querySelector('input[type="search"]'), null);
    } finally {
      await view.unmount();
    }
  });
}
