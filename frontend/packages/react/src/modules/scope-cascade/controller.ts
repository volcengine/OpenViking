import type { ScopeSwitcherOption } from '../../components/scope-switcher/index.js';
import type {
  ScopeCascadeApi,
  ScopeCascadeController,
  ScopeCascadeIndex,
  ScopeCascadeSnapshot,
} from './types.js';

const indices = [0, 1, 2] as const;
const empty = (
  enabled: boolean,
  parentIds: readonly string[] = [],
): ScopeCascadeSnapshot => ({
  options: [],
  enabled,
  loading: false,
  error: null,
  scopeKey: JSON.stringify(parentIds),
  parentIds,
});

/** Instance-owned channels. Creating a controller does not start requests. */
export function createScopeController() {
  const snapshots = [empty(true), empty(false), empty(false)];
  const listeners = indices.map(() => new Set<() => void>());
  const requests: (AbortController | undefined)[] = [];
  let api: ScopeCascadeApi | undefined;
  let active = false;
  const notify = (index: number) =>
    listeners[index]!.forEach((listener) => listener());
  const update = (index: number, patch: Partial<ScopeCascadeSnapshot>) => {
    snapshots[index] = { ...snapshots[index]!, ...patch };
  };
  const abort = (index: number) => {
    requests[index]?.abort();
    requests[index] = undefined;
  };
  const resetDescendants = (index: number) => {
    for (let next = index + 1; next < 3; next++) {
      abort(next);
      const ancestors = snapshots
        .slice(0, next)
        .map((item) => item.selected?.id);
      snapshots[next] = empty(
        ancestors.every((id) => id !== undefined),
        ancestors.filter((id): id is string => id !== undefined),
      );
    }
  };
  const load = async (index: ScopeCascadeIndex) => {
    if (!active || !api || !snapshots[index]!.enabled) return;
    abort(index);
    const request = new AbortController();
    requests[index] = request;
    update(index, { loading: true, error: null });
    notify(index);
    try {
      const libraryId = snapshots[0]!.selected?.id;
      const spaceId = snapshots[1]!.selected?.id;
      const options = await (index === 0
        ? api.loadLibraries(request.signal)
        : index === 1
          ? api.loadSpaces(libraryId!, request.signal)
          : api.loadUsers(libraryId!, spaceId!, request.signal));
      if (!active || request.signal.aborted || requests[index] !== request)
        return;
      const previous = snapshots[index]!.selected;
      const selected = options.find(
        (option) => option.id === previous?.id && !option.disabled,
      );
      update(index, { options, selected, loading: false, error: null });
      // A refreshed list may revoke the current selection. Clear all descendants
      // before notification so nobody sees a mixed library/space/user identity.
      const revoked = previous !== undefined && selected === undefined;
      if (revoked) resetDescendants(index);
      notify(index);
      if (revoked) for (let next = index + 1; next < 3; next++) notify(next);
    } catch (error) {
      if (!active || request.signal.aborted || requests[index] !== request)
        return;
      update(index, {
        loading: false,
        error: error instanceof Error ? error : new Error(String(error)),
      });
      notify(index);
    }
  };
  const select = (index: ScopeCascadeIndex, candidate: ScopeSwitcherOption) => {
    const state = snapshots[index]!;
    if (!active || !state.enabled || state.loading || state.error) return;
    const option = state.options.find(
      (item) => item.id === candidate.id && !item.disabled,
    );
    if (!option || option.id === state.selected?.id) return;
    update(index, { selected: option });
    resetDescendants(index);
    for (let next = index; next < 3; next++) notify(next);
    if (index < 2) void load((index + 1) as ScopeCascadeIndex);
  };
  const channel = (index: ScopeCascadeIndex) => ({
    getSnapshot: () => snapshots[index]!,
    subscribe: (listener: () => void) => {
      listeners[index]!.add(listener);
      return () => {
        listeners[index]!.delete(listener);
      };
    },
    select: (option: ScopeSwitcherOption) => select(index, option),
    refresh: () => {
      void load(index);
    },
  });
  const controller: ScopeCascadeController = {
    levels: [channel(0), channel(1), channel(2)],
  };
  return {
    controller,
    start(nextApi: ScopeCascadeApi) {
      api = nextApi;
      active = true;
      indices.forEach((index) => {
        abort(index);
        snapshots[index] = empty(index === 0);
      });
      indices.forEach(notify);
      void load(0);
      return () => {
        active = false;
        indices.forEach(abort);
      };
    },
  };
}
