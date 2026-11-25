import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import {
  deferred,
  mount,
  ok,
  recordingFetch,
  settle,
} from './helpers/module-dom.mjs';
import { directory, harness } from './helpers/current-user-tree.mjs';

const ROOT = 'viking://resources';
const CHILD = `${ROOT}/child`;
const scenarios = [
  { name: 'mode all to current', before: 'all', after: 'current' },
  { name: 'mode current to all', before: 'current', after: 'all' },
  { name: 'explicit cancellation', before: 'all' },
  { name: 'unmount', before: 'all', unmount: true },
];

for (const scenario of scenarios) {
  test(`preserved-root navigation waiting for initial load returns false after ${scenario.name}`, async (context) => {
    const initial = deferred();
    let first = true;
    const fetch = recordingFetch(({ path, uri }) => {
      assert.equal(path, '/api/v1/fs/ls');
      assert.equal(uri, ROOT);
      if (first) {
        first = false;
        return initial.promise;
      }
      return ok([directory(CHILD)]);
    });
    const state = harness({ fetch, rootUri: ROOT });
    const view = await mount(state.render({ userRootMode: scenario.before }));
    let pending,
      unmounted = false;
    try {
      assert.equal(state.tree.isReady, false);
      await act(async () => {
        pending = state.tree.reveal(ROOT, true);
      });
      await settle();
      if (scenario.after) {
        await view.render(state.render({ userRootMode: scenario.after }));
      } else if (scenario.unmount) {
        await view.unmount();
        unmounted = true;
      } else {
        await act(() => state.tree.cancelReveal());
      }
      await act(async () => initial.resolve(await ok([directory(CHILD)])));
      let result;
      await act(async () => {
        result = await pending;
      });
      await settle();
      const rootRequestsBeforeNext = state.lists().length;
      let nextResult;
      if (!unmounted) {
        await act(async () => {
          nextResult = await state.tree.reveal(ROOT, true);
        });
      }
      context.diagnostic(
        JSON.stringify({
          scenario: scenario.name,
          rootRequests: state.lists().length,
        }),
      );
      assert.equal(
        state.lists().length,
        rootRequestsBeforeNext,
        'preserving root expansion must not refresh it',
      );
      if (!scenario.after) assert.equal(rootRequestsBeforeNext, 1);
      assert.deepEqual(
        { result, nextResult },
        { result: false, nextResult: unmounted ? undefined : true },
      );
    } finally {
      initial.resolve(await ok([directory(CHILD)]));
      if (!unmounted) await view.unmount();
      await pending;
      state.queryClient.clear();
    }
  });
}

for (const userRootMode of ['all', 'current']) {
  test(`uncancelled preserved-root navigation succeeds when initial loading completes in ${userRootMode} mode`, async () => {
    const initial = deferred();
    const fetch = recordingFetch(() => initial.promise);
    const state = harness({ fetch, rootUri: ROOT });
    const view = await mount(state.render({ userRootMode }));
    let pending;
    try {
      assert.equal(state.tree.isReady, false);
      await act(async () => {
        pending = state.tree.reveal(ROOT, true);
      });
      await settle();
      await act(async () => initial.resolve(await ok([directory(CHILD)])));
      let result;
      await act(async () => {
        result = await pending;
      });
      assert.equal(state.tree.isReady, true);
      assert.equal(result, true);
      assert.deepEqual(state.lists(), [ROOT]);
      assert.ok(state.uris().includes(CHILD));
    } finally {
      initial.resolve(await ok([directory(CHILD)]));
      await view.unmount();
      await pending;
      state.queryClient.clear();
    }
  });
}
