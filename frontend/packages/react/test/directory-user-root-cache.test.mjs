import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { mount, deferred, ok, settle } from './helpers/module-dom.mjs';
import {
  ALICE,
  BOB,
  ROOT,
  USER,
  backend,
  directory,
  harness,
} from './helpers/current-user-tree.mjs';

test('other directories keep sharing their native cache across display modes', async () => {
  const current = harness({
    rootUri: ROOT,
    provider: { userRootMode: 'current' },
  });
  const all = harness({
    rootUri: ROOT,
    fetch: current.fetch,
    queryClient: current.queryClient,
  });
  const currentView = await mount(current.render());
  const allView = await mount(all.render());
  try {
    assert.deepEqual(current.uris(), [USER]);
    assert.deepEqual(all.uris(), [USER]);
    assert.deepEqual(current.lists(), [ROOT]);
    assert.equal(current.queryClient.getQueryCache().getAll().length, 1);
  } finally {
    await currentView.unmount();
    await allView.unmount();
    current.queryClient.clear();
  }
});

test('current and all modes isolate their caches while matching tree and picker share one', async () => {
  const current = harness({ provider: { userRootMode: 'current' } });
  const all = harness({
    fetch: current.fetch,
    queryClient: current.queryClient,
  });
  const currentView = await mount(current.render());
  let allView;
  try {
    await act(() => current.picker.load(USER));
    await settle();
    assert.deepEqual(current.picker.page.children, [ALICE]);
    assert.deepEqual(current.lists(), []);
    assert.equal(current.queryClient.getQueryCache().getAll().length, 1);
    allView = await mount(all.render());
    await act(() => all.picker.load(USER));
    await settle();
    assert.deepEqual(all.uris(), [ALICE, BOB]);
    assert.deepEqual(all.picker.page.children, [ALICE, BOB]);
    assert.deepEqual(current.uris(), [ALICE]);
    assert.deepEqual(all.lists(), [USER]);
    assert.equal(current.queryClient.getQueryCache().getAll().length, 2);
  } finally {
    await currentView.unmount();
    await allView?.unmount();
    current.queryClient.clear();
  }
});

test('changing display mode keeps the request client stable and changes only the directory cache', async () => {
  const state = harness();
  const view = await mount(state.render());
  try {
    const client = state.client;
    assert.deepEqual(state.uris(), [ALICE, BOB]);
    await view.render(state.render({ userRootMode: 'current' }));
    assert.equal(state.client, client);
    assert.deepEqual(state.uris(), [ALICE]);
    await view.render(state.render());
    assert.equal(state.client, client);
    assert.deepEqual(state.uris(), [ALICE, BOB]);
    assert.deepEqual(state.lists(), [USER]);
  } finally {
    await view.unmount();
    state.queryClient.clear();
  }
});

for (const [nested, expected] of [
  [{}, [ALICE]],
  [{ userRootMode: 'all' }, [ALICE, BOB]],
  [{ userId: '' }, [ALICE, BOB]],
]) {
  test(`nested Providers inherit or override current mode and identity: ${JSON.stringify(nested)}`, async () => {
    const state = harness({ provider: { userRootMode: 'current' }, nested });
    const view = await mount(state.render());
    try {
      assert.deepEqual(state.uris(), expected);
      assert.equal(state.lists().length, expected.length === 1 ? 0 : 1);
    } finally {
      await view.unmount();
      state.queryClient.clear();
    }
  });
}

test('identity changes discard an older native user listing before it resolves', async () => {
  const old = deferred();
  const fetch = backend((call) =>
    call.uri === USER ? old.promise : undefined,
  );
  const state = harness({
    fetch,
    provider: { userRootMode: 'current', userId: undefined },
  });
  const view = await mount(state.render());
  try {
    assert.deepEqual(state.lists(), [USER]);
    await view.render(state.render({ userId: 'alice@example.com' }));
    assert.deepEqual(state.uris(), [ALICE]);
    await act(async () => old.resolve(await ok([directory(BOB)])));
    await settle();
    assert.deepEqual(state.uris(), [ALICE]);
    await view.render(state.render({ userId: 'bob' }));
    assert.deepEqual(state.uris(), [BOB]);
    assert.deepEqual(state.lists(), [USER]);
  } finally {
    await view.unmount();
    state.queryClient.clear();
  }
});
