import type { ScopeSwitcherOption } from '../../components/scope-switcher/index.js';
import type {
  ScopeCascadeApi,
  ScopeCascadeController,
  ScopeCascadeIndex,
  ScopeCascadeSnapshot,
  ScopeCascadeSelection,
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
export function createScopeController(
  onChange?: (selection: ScopeCascadeSelection | null) => void,
  onUserSelection?: () => void,
) {
  const snapshots = [empty(true), empty(false), empty(false)];
  const listeners = indices.map(() => new Set<() => void>());
  const requests: (AbortController | undefined)[] = [];
  let api: ScopeCascadeApi | undefined;
  let active = false;
  let selectionKey = '';
  const notify = (index: number) => {
    listeners[index]!.forEach((listener) => listener());
    const [library, space, user] = snapshots;
    const selection =
      library!.selected && space!.selected && user!.selected
        ? {
            libraryId: library!.selected.id,
            spaceId: space!.selected.id,
            userId: user!.selected.id,
          }
        : null;
    const key = JSON.stringify(selection);
    if (key !== selectionKey) {
      selectionKey = key;
      onChange?.(selection);
    }
  };
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
      /**
       * 作用域始终有一个确定的值。此前的选中值不在新列表里、被禁用，或本来就没
       * 有时，改由第一个可选项承担——这一步必须在这里做：它决定后续请求的参数，
       * 组件只负责按给定值渲染，不做业务归一化。
       *
       * A scope always has a definite value. When the previous selection is gone
       * from the new list, is disabled, or was never set, the first selectable
       * option takes over. This has to happen here: it decides the parameters of
       * subsequent requests, and the component only renders the value it is given
       * rather than normalising business state.
       */
      const selected =
        options.find(
          (option) => option.id === previous?.id && !option.disabled,
        ) ?? options.find((option) => !option.disabled);
      const changed = selected !== undefined && selected.id !== previous?.id;
      update(index, { options, selected, loading: false, error: null });
      // A refreshed list may revoke the current selection. Clear all descendants
      // before notification so nobody sees a mixed library/space/user identity.
      // Picking a different option counts too: the descendants belong to the old
      // identity and must not survive the switch.
      const revoked = previous !== undefined && selected === undefined;
      if (revoked || changed) resetDescendants(index);
      notify(index);
      if (revoked || changed)
        for (let next = index + 1; next < 3; next++) notify(next);
      if (changed && index < 2) void load((index + 1) as ScopeCascadeIndex);
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
    onUserSelection?.();
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
