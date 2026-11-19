import assert from 'node:assert/strict';
import test from 'node:test';
import { suspenseNavigation } from './helpers/user-root-suspense.mjs';
import { transitionNavigation } from './helpers/user-root-transition.mjs';
import { act } from 'react';
import {
  deferred,
  mount,
  ok,
  recordingFetch,
  settle,
} from './helpers/module-dom.mjs';
import { USER, directory, harness } from './helpers/current-user-tree.mjs';

const HOME = 'viking://~';
const CANONICAL = 'viking://user/alice';
const STALE = `${CANONICAL}/docs`;
const FRESH = `${CANONICAL}/fresh`;
const CHILD = `${FRESH}/child.md`;

for (const [before, after] of [
  ['all', 'current'],
  ['current', 'all'],
]) {
  test(`switching user-root mode ${before} to ${after} cancels navigation and leaves new navigation usable`, async () => {
    const oldLookup = deferred();
    let lookupSignal;
    const fetch = recordingFetch(({ path, uri, init }) => {
      if (path.endsWith('/fs/stat') && uri === HOME) {
        if (!lookupSignal) {
          lookupSignal = init.signal;
          return oldLookup.promise;
        }
        return ok({ uri: CANONICAL, isDir: true });
      }
      if (path.endsWith('/fs/ls')) {
        if (uri === USER) return ok([directory(CANONICAL)]);
        if (uri === CANONICAL) return ok([directory(STALE), directory(FRESH)]);
        if (uri === STALE) return ok([{ uri: `${STALE}/file.md` }]);
        return ok([{ uri: CHILD }]);
      }
      return ok('');
    });
    const state = harness({
      fetch,
      rootUri: USER,
      provider: { userId: undefined },
    });
    const view = await mount(state.render({ userRootMode: before }));
    let pending;
    try {
      await act(async () => {
        pending = state.tree.reveal(`${HOME}/docs/file.md`);
      });
      await settle();
      assert.ok(
        lookupSignal,
        'the old navigation is waiting for real home resolution',
      );
      assert.equal(lookupSignal.aborted, false);
      await view.render(state.render({ userRootMode: after }));
      const abortedOnSwitch = lookupSignal.aborted;
      await act(async () =>
        oldLookup.resolve(await ok({ uri: CANONICAL, isDir: true })),
      );
      let oldResult;
      await act(async () => {
        oldResult = await pending;
      });
      await settle();
      const oldChildRequests = state
        .lists()
        .filter((uri) => uri !== USER).length;
      let newResult;
      await act(async () => {
        newResult = await state.tree.reveal(`${HOME}/fresh/child.md`);
      });
      await settle();
      assert.deepEqual(
        {
          abortedOnSwitch,
          oldResult,
          oldChildRequests,
          newResult,
          childVisible: state.uris().includes(CHILD),
        },
        {
          abortedOnSwitch: true,
          oldResult: false,
          oldChildRequests: 0,
          newResult: true,
          childVisible: true,
        },
      );
    } finally {
      oldLookup.resolve(await ok({ uri: CANONICAL, isDir: true }));
      await view.unmount();
      await pending;
      state.queryClient.clear();
    }
  });
}

for (const [before, after] of [
  ['all', 'current'],
  ['current', 'all'],
]) {
  test(`a concurrent ${before} to ${after} mode commit cancels navigation before layout releases its response`, async (context) => {
    const observed = await transitionNavigation(before, after);
    context.diagnostic(JSON.stringify(observed));
    assert.deepEqual(observed, {
      abortedAtLayout: true,
      oldResult: false,
      oldChildren: [],
      newResult: true,
      childVisible: true,
    });
  });
}

for (const mode of ['all', 'current']) {
  test(`a temporary Suspense fallback preserves ${mode} mode navigation until recovery`, async (context) => {
    const observed = await suspenseNavigation(mode);
    context.diagnostic(JSON.stringify(observed));
    assert.deepEqual(observed, {
      pendingBeforeHide: true,
      fallbackShown: true,
      abortedWhileHidden: false,
      restored: true,
      clientPreserved: true,
      result: true,
      childVisible: true,
      childRequests: ['viking://user/alice', 'viking://user/alice/docs'],
    });
  });
}
