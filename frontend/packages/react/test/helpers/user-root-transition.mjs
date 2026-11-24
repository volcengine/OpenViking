import {
  act,
  createElement as h,
  Profiler,
  startTransition,
  useEffect,
  useLayoutEffect,
} from 'react';
import { deferred, ok, recordingFetch } from './module-dom.mjs';
import { USER, directory, harness } from './current-user-tree.mjs';

const { createRoot } = await import('react-dom/client');
const HOME = 'viking://~';
const OWNER = `${USER}/alice`;
const OLD_DIR = `${OWNER}/docs`;
const NEW_DIR = `${OWNER}/fresh`;
const NEW_FILE = `${NEW_DIR}/child.md`;

export async function transitionNavigation(before, after) {
  const lookup = deferred();
  const requested = deferred();
  const initialReady = deferred();
  const committed = deferred();
  const nextReady = deferred();
  const visible = deferred();
  const phases = [];
  let lookupSignal,
    abortedAtLayout,
    phase = 'initial';
  const fetch = recordingFetch(({ path, uri, init }) => {
    if (path.endsWith('/fs/stat') && uri === HOME) {
      if (!lookupSignal) {
        lookupSignal = init.signal;
        requested.resolve();
        return lookup.promise;
      }
      return ok({ uri: OWNER, isDir: true });
    }
    if (path.endsWith('/fs/ls')) {
      phases.push({ phase, uri });
      if (uri === USER) return ok([directory(OWNER)]);
      if (uri === OWNER) return ok([directory(OLD_DIR), directory(NEW_DIR)]);
      if (uri === OLD_DIR) return ok([{ uri: `${OLD_DIR}/file.md` }]);
      return ok([{ uri: NEW_FILE }]);
    }
    return ok('');
  });
  const state = harness({
    fetch,
    rootUri: USER,
    provider: { userId: undefined },
  });
  function LayoutBoundary({ mode, children }) {
    // Release the real pending backend response after child directory layout work.
    // The mode commit opens the race; root teardown owns request cancellation.
    useLayoutEffect(() => {
      if (mode !== after) return;
      phase = 'layout';
      abortedAtLayout = lookupSignal.aborted;
      lookup.resolve(
        Response.json({ status: 'ok', result: { uri: OWNER, isDir: true } }),
      );
    }, [mode]);
    // Mark the actual passive boundary without act forcing effects to flush early.
    // React owns the lifecycle and root teardown removes this boundary.
    useEffect(() => {
      phase = `passive:${mode}`;
      if (mode === after) committed.resolve();
    }, [mode]);
    return children;
  }
  const render = (mode) =>
    h(
      Profiler,
      {
        id: 'user-root-transition',
        onRender() {
          if (!state.tree?.isReady) return;
          (mode === before ? initialReady : nextReady).resolve();
          if (state.uris().includes(NEW_FILE)) visible.resolve();
        },
      },
      h(LayoutBoundary, { mode }, state.render({ userRootMode: mode })),
    );
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  const previousAct = globalThis.IS_REACT_ACT_ENVIRONMENT;
  // Preserve real transition scheduling instead of act's synchronous effect flush.
  // This test-environment hint is restored in finally before root cleanup.
  globalThis.IS_REACT_ACT_ENVIRONMENT = false;
  let pending;
  try {
    startTransition(() => root.render(render(before)));
    await initialReady.promise;
    pending = state.tree.reveal(`${HOME}/docs/file.md`);
    await requested.promise;
    startTransition(() => root.render(render(after)));
    await committed.promise;
    const oldResult = await pending;
    const oldChildren = phases.filter(({ uri }) => uri !== USER);
    await nextReady.promise;
    const newResult = await state.tree.reveal(`${HOME}/fresh/child.md`);
    if (newResult) await visible.promise;
    return {
      abortedAtLayout,
      oldResult,
      oldChildren,
      newResult,
      childVisible: state.uris().includes(NEW_FILE),
    };
  } finally {
    lookup.resolve(
      Response.json({ status: 'ok', result: { uri: OWNER, isDir: true } }),
    );
    globalThis.IS_REACT_ACT_ENVIRONMENT = previousAct;
    await act(async () => root.unmount());
    await pending;
    container.remove();
    state.queryClient.clear();
  }
}
