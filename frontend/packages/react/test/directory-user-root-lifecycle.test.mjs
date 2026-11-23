import assert from 'node:assert/strict';
import test from 'node:test';
import {
  act,
  createElement as h,
  startTransition,
  Suspense,
  useState,
} from 'react';
import { deferred, mount, settle } from './helpers/module-dom.mjs';
import { ALICE, harness } from './helpers/current-user-tree.mjs';
import {
  FILE,
  blockedListing,
  sharedNavigation,
  suspenseView,
} from './helpers/user-root-lifecycle.mjs';

for (const userRootMode of ['all', 'current']) {
  test(`a ${userRootMode} navigation can finish its directory request while Suspense is hidden`, async () => {
    const listing = blockedListing();
    const state = harness({ fetch: listing.fetch, provider: { userRootMode } });
    const boundary = suspenseView(state);
    const view = await mount(boundary.render(false));
    let pending;
    try {
      await act(async () => {
        pending = state.tree.reveal(FILE);
      });
      const signal = await listing.started.promise;
      // An urgent commit hides the mounted tree; no hook effects are mocked.
      await view.render(boundary.render(true));
      assert.equal(boundary.hidden(view), true);
      assert.equal(signal.aborted, false);
      await act(listing.release);
      let result;
      await act(async () => {
        result = await pending;
      });
      assert.equal(result, true);
      assert.equal(boundary.hidden(view), true);
      await view.render(boundary.render(false));
      await act(async () => boundary.release());
      await settle();
      assert.ok(state.uris().includes(FILE));
      assert.equal(
        listing.fetch.calls.filter((call) => call.uri === ALICE).length,
        1,
      );
    } finally {
      await act(listing.release);
      await view.unmount();
      await act(async () => boundary.release());
      await pending;
      state.queryClient.clear();
    }
  });
}

for (const action of ['cancel', 'timeout', 'mode switch', 'unmount']) {
  test(`a hidden current-mode consumer can ${action} without cancelling a shared all-mode listing`, async () => {
    await sharedNavigation(action);
  });
}

for (const [before, after] of [
  ['all', 'current'],
  ['current', 'all'],
]) {
  test(`an abandoned ${before} to ${after} mode render preserves committed navigation`, async () => {
    const listing = blockedListing();
    const state = harness({ fetch: listing.fetch });
    const gate = deferred();
    let update,
      attempted = false;
    function Gate({ blocked }) {
      if (blocked) {
        attempted = true;
        throw gate.promise;
      }
      return null;
    }
    function Host() {
      const [next, setNext] = useState({ mode: before, blocked: false });
      update = setNext;
      return h(
        Suspense,
        { fallback: h('p', { 'data-abandon-fallback': true }, 'Loading') },
        state.render({ userRootMode: next.mode }),
        h(Gate, { blocked: next.blocked }),
      );
    }
    const view = await mount(h(Host));
    let pending;
    try {
      const committedClient = state.client;
      await act(async () => {
        pending = state.tree.reveal(FILE);
      });
      const signal = await listing.started.promise;
      const nativeLists = state.lists().length;
      // Real React scheduling attempts the new scope, then Suspense prevents
      // its layout/passive effects from committing before the old request ends.
      await act(async () => {
        startTransition(() => update({ mode: after, blocked: true }));
      });
      assert.equal(
        attempted,
        true,
        'the alternate scope really rendered and suspended',
      );
      assert.equal(
        view.container.querySelector('[data-abandon-fallback]'),
        null,
      );
      assert.equal(signal.aborted, false);
      assert.equal(
        state.lists().length,
        nativeLists,
        'an uncommitted scope cannot start transport',
      );
      await act(listing.release);
      let result;
      await act(async () => {
        result = await pending;
      });
      assert.equal(result, true);
      await act(() => update({ mode: before, blocked: false }));
      await settle();
      assert.equal(state.client, committedClient);
      assert.ok(state.uris().includes(FILE));
      assert.equal(
        listing.fetch.calls.filter((call) => call.uri === ALICE).length,
        1,
      );
    } finally {
      await act(listing.release);
      await view.unmount();
      await act(async () => gate.resolve());
      await pending;
      state.queryClient.clear();
    }
  });
}
