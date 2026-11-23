import { act, createElement as h, Suspense } from 'react';
import { deferred, mount, ok, recordingFetch, settle } from './module-dom.mjs';
import { USER, directory, harness } from './current-user-tree.mjs';

const HOME = 'viking://~';
const OWNER = `${USER}/alice`;
const DOCS = `${OWNER}/docs`;
const FILE = `${DOCS}/file.md`;

export async function suspenseNavigation(userRootMode) {
  const lookup = deferred();
  const gate = deferred();
  let lookupSignal;
  const fetch = recordingFetch(({ path, uri, init }) => {
    if (path.endsWith('/fs/stat') && uri === HOME) {
      lookupSignal = init.signal;
      return lookup.promise;
    }
    if (path.endsWith('/fs/ls')) {
      if (uri === USER) return ok([directory(OWNER)]);
      if (uri === OWNER) return ok([directory(DOCS)]);
      return ok([{ uri: FILE }]);
    }
    return ok('');
  });
  const state = harness({
    fetch,
    rootUri: USER,
    provider: { userId: undefined },
  });
  function Gate({ blocked }) {
    if (blocked) throw gate.promise;
    return null;
  }
  // React renders the real useFsTree Probe first, then this sibling can suspend
  // its already-visible boundary without unmounting the directory component.
  const render = (blocked) =>
    h(
      Suspense,
      { fallback: h('div', { 'data-navigation-fallback': true }, 'Loading') },
      state.render({ userRootMode }),
      h(Gate, { blocked }),
    );
  const view = await mount(render(false));
  let pending;
  try {
    const client = state.client;
    await act(async () => {
      pending = state.tree.reveal(`${HOME}/docs/file.md`);
    });
    await settle();
    const pendingBeforeHide = Boolean(lookupSignal) && !lookupSignal.aborted;
    // This ordinary update is urgent: no transition keeps the old contents visible.
    await view.render(render(true));
    const fallbackShown = Boolean(
      view.container.querySelector('[data-navigation-fallback]'),
    );
    const abortedWhileHidden = lookupSignal.aborted;
    await view.render(render(false));
    await act(async () => gate.resolve());
    const restored = !view.container.querySelector(
      '[data-navigation-fallback]',
    );
    await act(async () =>
      lookup.resolve(await ok({ uri: OWNER, isDir: true })),
    );
    let result;
    await act(async () => {
      result = await pending;
    });
    await settle();
    return {
      pendingBeforeHide,
      fallbackShown,
      abortedWhileHidden,
      restored,
      clientPreserved: state.client === client,
      result,
      childVisible: state.uris().includes(FILE),
      childRequests: state.lists().filter((uri) => uri !== USER),
    };
  } finally {
    await act(async () => {
      gate.resolve();
      lookup.resolve(await ok({ uri: OWNER, isDir: true }));
    });
    await view.unmount();
    await pending;
    state.queryClient.clear();
  }
}
