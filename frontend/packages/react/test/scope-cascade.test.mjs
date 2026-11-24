import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRslib } from '@rslib/core';

// Exercise the private coordinator without adding a public implementation export.
const output = await mkdtemp(join(tmpdir(), 'ov-scope-cascade-'));
const builder = await createRslib({
  config: {
    lib: [
      {
        format: 'esm',
        dts: false,
        source: {
          entry: {
            controller: fileURLToPath(
              new URL(
                '../src/modules/scope-cascade/controller.ts',
                import.meta.url,
              ),
            ),
          },
        },
      },
    ],
    output: { target: 'node', distPath: { root: output } },
  },
});
await builder.build();
await writeFile(join(output, 'package.json'), '{"type":"module"}');
const { createScopeController } = await import(
  pathToFileURL(join(output, 'controller.js'))
);
after(() => rm(output, { recursive: true, force: true }));
const option = (id) => ({ id, label: id });
const tick = () => new Promise((resolve) => setImmediate(resolve));
const api = () => ({
  loadLibraries: () => Promise.resolve([option('a'), option('b')]),
  loadSpaces: (id) => Promise.resolve([option(`${id}-space`)]),
  loadUsers: (library, space) =>
    Promise.resolve([option(`${library}/${space}/user`)]),
});
async function selectAll(instance) {
  const [library, space, user] = instance.controller.levels;
  await tick();
  library.select(option('a'));
  await tick();
  space.select(option('a-space'));
  await tick();
  user.select(option('a/a-space/user'));
}

test('lazy start, automatic cascading, stable unrelated channels and same-value no-op', async () => {
  const instance = createScopeController();
  const [library, space, user] = instance.controller.levels;
  assert.equal(library.getSnapshot().loading, false);
  assert.equal(space.getSnapshot().enabled, false);
  const stop = instance.start(api());
  await selectAll(instance);
  const snapshots = instance.controller.levels.map((level) =>
    level.getSnapshot(),
  );
  user.refresh();
  await tick();
  assert.equal(library.getSnapshot(), snapshots[0]);
  assert.equal(space.getSnapshot(), snapshots[1]);
  const current = user.getSnapshot();
  user.select(current.selected);
  assert.equal(user.getSnapshot(), current);
  assert.deepEqual(user.getSnapshot().parentIds, ['a', 'a-space']);
  stop();
});

test('switching parent aborts old requests and ignores responses from adapters that ignore signals', async () => {
  const pending = new Map();
  const instance = createScopeController();
  const [library, space, user] = instance.controller.levels;
  const stop = instance.start({
    ...api(),
    loadSpaces: (id, signal) =>
      new Promise((resolve) => pending.set(id, { resolve, signal })),
  });
  await tick();
  library.select(option('a'));
  library.select(option('b'));
  assert.equal(pending.get('a').signal.aborted, true);
  assert.equal(user.getSnapshot().enabled, false);
  pending.get('b').resolve([option('b-space')]);
  await tick();
  pending.get('a').resolve([option('stale')]);
  await tick();
  assert.deepEqual(space.getSnapshot().options, [option('b-space')]);
  stop();
});

test('failures and retry affect only the requesting channel', async () => {
  let fail = true;
  const instance = createScopeController();
  const [library, space, user] = instance.controller.levels;
  const stop = instance.start({
    ...api(),
    loadSpaces: () =>
      fail
        ? Promise.reject(new Error('offline'))
        : Promise.resolve([option('space')]),
  });
  await tick();
  library.select(option('a'));
  await tick();
  assert.equal(space.getSnapshot().error.message, 'offline');
  const before = library.getSnapshot();
  fail = false;
  space.refresh();
  await tick();
  assert.equal(space.getSnapshot().error, null);
  // The recovered list has no prior selection, so the first option takes over
  // and enables the user level. That is the cascade working, not a leak: the
  // library channel, which did not request anything, keeps its snapshot.
  assert.equal(space.getSnapshot().selected?.id, 'space');
  assert.equal(library.getSnapshot(), before);
  stop();
});

test('refresh revoking selection clears all descendants before notification', async () => {
  let items = [option('a')];
  const instance = createScopeController();
  const [library, space, user] = instance.controller.levels;
  const stop = instance.start({
    ...api(),
    loadLibraries: () => Promise.resolve(items),
  });
  await selectAll(instance);
  const unsubscribe = library.subscribe(() => {
    if (!library.getSnapshot().selected) {
      assert.equal(space.getSnapshot().selected, undefined);
      assert.equal(user.getSnapshot().selected, undefined);
      assert.equal(user.getSnapshot().enabled, false);
    }
  });
  items = [];
  library.refresh();
  await tick();
  assert.equal(library.getSnapshot().selected, undefined);
  unsubscribe();
  stop();
});

test('a revoked or disabled selection falls back to the first option', async () => {
  // Normalising the selection lives here, not in the view: it decides the
  // parameters of downstream requests.
  let items = [option('a'), option('b')];
  const instance = createScopeController();
  const [library] = instance.controller.levels;
  const stop = instance.start({
    ...api(),
    loadLibraries: () => Promise.resolve(items),
  });
  await tick();
  assert.equal(library.getSnapshot().selected?.id, 'a');

  // 'a' disappears from the refreshed list: the first remaining option takes over.
  items = [option('b')];
  library.refresh();
  await tick();
  assert.equal(library.getSnapshot().selected?.id, 'b');

  // A disabled first option is skipped in favour of the next usable one.
  items = [{ id: 'x', label: 'x', disabled: true }, option('y')];
  library.refresh();
  await tick();
  assert.equal(library.getSnapshot().selected?.id, 'y');

  // An empty list leaves nothing to select.
  items = [];
  library.refresh();
  await tick();
  assert.equal(library.getSnapshot().selected, undefined);
  stop();
});

test('cleanup/restart rejects previous completions and instances stay independent', async () => {
  const pending = [];
  const instance = createScopeController();
  const other = createScopeController();
  const untouched = other.controller.levels[0].getSnapshot();
  const adapters = {
    ...api(),
    loadLibraries: (signal) =>
      new Promise((resolve) => pending.push({ resolve, signal })),
  };
  instance.start(adapters)();
  const stop = instance.start(adapters);
  assert.equal(pending[0].signal.aborted, true);
  pending[1].resolve([option('new')]);
  await tick();
  pending[0].resolve([option('old')]);
  await tick();
  assert.deepEqual(instance.controller.levels[0].getSnapshot().options, [
    option('new'),
  ]);
  assert.equal(other.controller.levels[0].getSnapshot(), untouched);
  stop();
  const before = instance.controller.levels[0].getSnapshot();
  instance.controller.levels[0].refresh();
  assert.equal(instance.controller.levels[0].getSnapshot(), before);
});
