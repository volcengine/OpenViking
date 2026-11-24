import assert from 'node:assert/strict';
import { act, createElement as h, Suspense } from 'react';
import { deferred, mount, ok, settle } from './module-dom.mjs';
import {
  ALICE,
  USER,
  backend,
  directory,
  harness,
} from './current-user-tree.mjs';

export const DOCS = `${ALICE}/docs`;
export const FILE = `${DOCS}/file.md`;

export function blockedListing() {
  const response = deferred();
  const started = deferred();
  const fetch = backend((call) => {
    if (call.uri === ALICE) {
      started.resolve(call.init.signal);
      return response.promise;
    }
    if (call.uri === DOCS) return ok([{ uri: FILE }]);
  });
  return {
    fetch,
    started,
    release: async () => response.resolve(await ok([directory(DOCS)])),
  };
}

export function suspenseView(state) {
  const gate = deferred();
  // The sibling suspends after the real directory hook renders. React owns
  // hiding and recovery; the test never substitutes lifecycle effects.
  function Gate({ blocked }) {
    if (blocked) throw gate.promise;
    return null;
  }
  return {
    render: (blocked, provider) =>
      h(
        Suspense,
        { fallback: h('p', { 'data-audit-fallback': true }, 'Loading') },
        state.render(provider),
        h(Gate, { blocked }),
      ),
    release: () => gate.resolve(),
    hidden: (view) =>
      Boolean(view.container.querySelector('[data-audit-fallback]')),
  };
}

export async function sharedNavigation(action) {
  const listing = blockedListing();
  const controller = new AbortController();
  const current = harness({
    fetch: listing.fetch,
    provider: { userRootMode: 'current' },
    requestOptions: { signal: controller.signal },
  });
  const all = harness({
    fetch: listing.fetch,
    queryClient: current.queryClient,
  });
  const boundary = suspenseView(current);
  const currentView = await mount(boundary.render(false));
  const allView = await mount(all.render());
  let old, live;
  const timeout = new DOMException('Audit timeout', 'TimeoutError');
  const committedClient = current.client;
  try {
    await act(async () => {
      old = current.tree.reveal(FILE).then(
        (result) => ({ result }),
        (error) => ({ error }),
      );
      live = all.tree.reveal(FILE);
    });
    const signal = await listing.started.promise;
    assert.equal(
      listing.fetch.calls.filter((call) => call.uri === ALICE).length,
      1,
    );
    await currentView.render(boundary.render(true));
    assert.equal(boundary.hidden(currentView), true);
    assert.equal(signal.aborted, false);
    if (action === 'cancel') await act(() => current.tree.cancelReveal());
    else if (action === 'timeout') await act(() => controller.abort(timeout));
    else if (action === 'unmount') await currentView.unmount();
    else {
      await currentView.render(boundary.render(false, { userRootMode: 'all' }));
      await act(async () => boundary.release());
      assert.equal(current.client, committedClient);
    }
    assert.equal(
      signal.aborted,
      false,
      'the other consumer still owns the transport',
    );
    await act(listing.release);
    let oldResult, liveResult;
    await act(async () => {
      oldResult = await old;
      liveResult = await live;
    });
    await settle();
    assert.deepEqual(
      oldResult,
      action === 'timeout' ? { error: timeout } : { result: false },
    );
    assert.equal(liveResult, true);
    assert.ok(all.uris().includes(FILE));
    assert.equal(
      listing.fetch.calls.filter((call) => call.uri === ALICE).length,
      1,
    );
    assert.equal(
      listing.fetch.calls.filter((call) => call.uri === DOCS).length,
      1,
    );
    assert.equal(
      listing.fetch.calls.filter((call) => call.uri === USER).length,
      1,
    );
  } finally {
    await act(listing.release);
    if (action !== 'unmount') await currentView.unmount();
    await act(async () => boundary.release());
    await allView.unmount();
    await Promise.all([old, live]);
    current.queryClient.clear();
  }
}
