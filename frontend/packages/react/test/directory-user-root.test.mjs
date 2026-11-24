import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { mount, settle } from './helpers/module-dom.mjs';
import {
  ALICE,
  BOB,
  ROOT,
  USER,
  harness,
  loadPage,
} from './helpers/current-user-tree.mjs';

test('default mode lists every native user even with a configured identity', async () => {
  const state = harness();
  const view = await mount(state.render());
  try {
    assert.deepEqual(state.uris(), [ALICE, BOB]);
    assert.deepEqual(state.lists(), [USER]);
  } finally {
    await view.unmount();
    state.queryClient.clear();
  }
});

test('current display mode leaves explicit Client listing requests native', async () => {
  const state = harness({ provider: { userRootMode: 'current' } });
  const view = await mount(state.render());
  try {
    const page = await state.client.requestEnvelope({
      method: 'GET',
      path: '/api/v1/fs/ls',
      query: { uri: USER },
    });
    assert.deepEqual(
      page.result.map(({ uri }) => uri),
      [ALICE, BOB],
    );
    assert.deepEqual(state.uris(), [ALICE]);
    assert.deepEqual(state.lists(), [USER]);
  } finally {
    await view.unmount();
    state.queryClient.clear();
  }
});

test('current mode expands the user group locally and loads concrete directories normally', async () => {
  const state = harness({
    rootUri: ROOT,
    provider: { userRootMode: 'current' },
  });
  const view = await mount(state.render());
  try {
    await act(() => state.tree.toggleExpand({ uri: USER }));
    await settle();
    assert.deepEqual(state.lists(), [ROOT]);
    assert.deepEqual(state.uris(), [USER, ALICE]);
    assert.equal(
      state.tree.rows.find((row) => row.uri === ALICE).name,
      'alice@example.com',
    );
    await act(() => state.tree.toggleExpand({ uri: ALICE }));
    await settle();
    assert.deepEqual(state.lists(), [ROOT, ALICE]);
    assert.ok(state.uris().includes(`${ALICE}/resources`));
    await act(() => state.tree.open(BOB));
    await act(() => state.tree.open('viking://resources'));
    assert.deepEqual(state.lists(), [ROOT, ALICE, BOB, 'viking://resources']);
  } finally {
    await view.unmount();
    state.queryClient.clear();
  }
});

for (const treeOptions of [
  { defaultExpandedUri: ALICE },
  { defaultExpandedUris: [USER, ALICE] },
]) {
  test(`current mode restores ${JSON.stringify(treeOptions)} without listing all users`, async () => {
    const state = harness({
      rootUri: ROOT,
      provider: { userRootMode: 'current' },
      treeOptions,
    });
    const view = await mount(state.render());
    try {
      assert.deepEqual(state.lists(), [ROOT, ALICE]);
      assert.ok(state.uris().includes(`${ALICE}/resources`));
    } finally {
      await view.unmount();
      state.queryClient.clear();
    }
  });
}

for (const userId of [undefined, '', '   ']) {
  test(`current mode retains native discovery for unknown identity ${JSON.stringify(userId)}`, async () => {
    const state = harness({ provider: { userRootMode: 'current', userId } });
    const view = await mount(state.render());
    try {
      assert.deepEqual(state.uris(), [ALICE, BOB]);
      assert.deepEqual(state.lists(), [USER]);
    } finally {
      await view.unmount();
      state.queryClient.clear();
    }
  });
}

test('current mode inherits a resolved asynchronous Provider identity', async () => {
  const state = harness({
    provider: {
      userRootMode: 'current',
      userId: async () => 'alice@example.com',
    },
  });
  const view = await mount(state.render());
  try {
    assert.deepEqual(state.uris(), [ALICE]);
    assert.deepEqual(state.fetch.calls, []);
  } finally {
    await view.unmount();
    state.queryClient.clear();
  }
});

test('current mode uses resolved per-request identity instead of the Provider identity', async () => {
  const state = harness({
    rootUri: `${USER}/`,
    provider: { userRootMode: 'current', userId: async () => 'inherited' },
    requestOptions: { userId: async () => ' alice@example.com ' },
  });
  const view = await mount(state.render());
  try {
    assert.deepEqual(state.uris(), [ALICE]);
    assert.deepEqual(state.fetch.calls, []);
  } finally {
    await view.unmount();
    state.queryClient.clear();
  }
});

test('local pages end after the first result and respect cancellation before returning', async () => {
  const client = {
    requestEnvelope: () => assert.fail('local group must not issue a request'),
  };
  const controller = new AbortController();
  const options = { userId: 'alice@example.com' };
  for (const offset of [0, 200]) {
    const page = await loadPage(
      client,
      `${USER}/`,
      offset,
      controller.signal,
      options,
      undefined,
      'current',
    );
    assert.deepEqual(page, {
      entries: offset ? [] : [{ uri: ALICE, isDir: true }],
      hasMore: false,
    });
  }
  controller.abort();
  await assert.rejects(
    loadPage(client, USER, 0, controller.signal, options, undefined, 'current'),
    { name: 'AbortError' },
  );
});
